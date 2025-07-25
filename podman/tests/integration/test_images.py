#   Copyright 2020 Red Hat, Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.
#
"""Images integration tests."""

import io
import json
import platform
import tarfile
import types
import unittest

import podman.tests.integration.base as base
from podman import PodmanClient
from podman.domain.images import Image
from podman.errors import APIError, ImageNotFound, PodmanError


# @unittest.skipIf(os.geteuid() != 0, 'Skipping, not running as root')


class ImagesIntegrationTest(base.IntegrationTest):
    """images call integration test"""

    def setUp(self):
        super().setUp()

        self.client = PodmanClient(base_url=self.socket_uri)
        self.addCleanup(self.client.close)

    def test_image_crud(self):
        """Test Image CRUD.

        Notes:
            Written to maximize reuse of pulled image.
        """

        with self.subTest("Pull Alpine Image"):
            image = self.client.images.pull("quay.io/libpod/alpine", tag="latest")
            self.assertIsInstance(image, Image)
            self.assertIn("quay.io/libpod/alpine:latest", image.tags)
            self.assertTrue(self.client.images.exists(image.id))

        with self.subTest("Inspect Alpine Image"):
            image = self.client.images.get("quay.io/libpod/alpine")
            self.assertIsInstance(image, Image)
            self.assertIn("quay.io/libpod/alpine:latest", image.tags)

        with self.subTest("Retrieve Image history"):
            ids = [i["Id"] for i in image.history()]
            self.assertIn(image.id, ids)

        with self.subTest("Export Image to tarball (in memory)"):
            buffer = io.BytesIO()
            for chunk in image.save():
                buffer.write(chunk)
            buffer.seek(0, 0)

            with tarfile.open(fileobj=buffer, mode="r") as tar:
                items = tar.getnames()
            self.assertIn("manifest.json", items)
            self.assertIn("repositories", items)

        with self.subTest("List images"):
            image_list = self.client.images.list()
            self.assertTrue(
                any([i for i in image_list if "quay.io/libpod/alpine:latest" in i.tags]),
                f"pulled image 'quay.io/libpod/alpine:latest' not listed, {image_list}.",
            )

        with self.subTest("Tag and reload() Image"):
            self.assertTrue(image.tag("localhost/alpine", "unittest"))
            image.reload()
            self.assertIn("localhost/alpine:unittest", image.tags)

        with self.subTest("Delete Image"):
            actual = self.client.images.remove(image.id, force=True)
            deleted = [d["Deleted"] for d in actual if "Deleted" in d]
            self.assertIn(image.id, deleted)
            untagged = [d["Untagged"] for d in actual if "Untagged" in d]
            self.assertIn(image.tags[0], untagged)
            exit_code = [d["ExitCode"] for d in actual if "ExitCode" in d]
            self.assertIn(0, exit_code)

            with self.assertRaises(ImageNotFound):
                # verify image deleted before loading below
                self.client.images.get(image.id)

        with self.subTest("Load Image previously deleted"):
            buffer.seek(0, 0)
            new_image = next(iter(self.client.images.load(buffer.getvalue())))
            self.assertEqual(image.id, new_image.id)

        with self.subTest("Deleted unused Images"):
            actual = self.client.images.prune()
            deleted = [d.get("Deleted") for d in actual["ImagesDeleted"]]
            self.assertIn(image.id, deleted)
            self.assertGreater(actual["SpaceReclaimed"], 0)

        with self.subTest("Export Image to tarball (in memory) with named mode"):
            alpine_image = self.client.images.pull("quay.io/libpod/alpine", tag="latest")
            image_buffer = io.BytesIO()
            for chunk in alpine_image.save(named=True):
                image_buffer.write(chunk)
            image_buffer.seek(0, 0)

            with tarfile.open(fileobj=image_buffer, mode="r") as tar:
                items_in_tar = tar.getnames()
                # Check if repositories file is available in the tarball
                self.assertIn("repositories", items_in_tar)
                # Extract the 'repositories' file
                repositories_file = tar.extractfile("repositories")
                if repositories_file is not None:
                    # Check the content of the "repositories" file.
                    repositories_content = repositories_file.read().decode("utf-8")
                    # Check if "repositories" file contains the name of the Image (named).
                    self.assertTrue("alpine" in str(repositories_content))

    def test_search(self):
        # N/B: This is an infrequently used feature, that tends to flake a lot.
        # Just check that it doesn't throw an exception and move on.
        self.client.images.search("alpine")

    @unittest.skip("Needs Podman 3.1.0")
    def test_corrupt_load(self):
        with self.assertRaises(APIError) as e:
            next(self.client.images.load(b"This is a corrupt tarball"))
        self.assertIn("payload does not match", e.exception.explanation)

    def test_build(self):
        buffer = io.StringIO("""FROM quay.io/libpod/alpine_labels:latest""")

        image, stream = self.client.images.build(fileobj=buffer)
        self.assertIsNotNone(image)
        self.assertIsNotNone(image.id)

    def test_build_cache(self):
        """Check that building twice the same image uses caching"""
        buffer = io.StringIO("""FROM quay.io/libpod/alpine_labels:latest\nLABEL test=value""")
        image, _ = self.client.images.build(fileobj=buffer)
        buffer.seek(0)
        _, stream = self.client.images.build(fileobj=buffer)
        for line in stream:
            # Search for a line with contents "-> Using cache <image id>"
            parsed = json.loads(line)['stream']
            if "Using cache" in parsed:
                break
        self.assertEqual(parsed.split()[3], image.id)

    def test_build_with_context(self):
        context = io.BytesIO()
        with tarfile.open(fileobj=context, mode="w") as tar:

            def add_file(name: str, content: str):
                binary_content = content.encode("utf-8")
                fileobj = io.BytesIO(binary_content)
                tarinfo = tarfile.TarInfo(name=name)
                tarinfo.size = len(binary_content)
                tar.addfile(tarinfo, fileobj)

            # Use a non-standard Dockerfile name to test the 'dockerfile' argument
            add_file(
                "MyDockerfile", ("FROM quay.io/libpod/alpine_labels:latest\nCOPY example.txt .\n")
            )
            add_file("example.txt", "This is an example file.\n")

        # Rewind to the start of the generated file so we can read it
        context.seek(0)

        with self.assertRaises(PodmanError) as e:
            # If requesting a custom context, must provide the context as `fileobj`
            self.client.images.build(custom_context=True, path='invalid')

        with self.assertRaises(PodmanError) as e:
            # If requesting a custom context, currently must specify the dockerfile name
            self.client.images.build(custom_context=True, fileobj=context)

        image, stream = self.client.images.build(
            fileobj=context,
            dockerfile="MyDockerfile",
            custom_context=True,
        )
        self.assertIsNotNone(image)
        self.assertIsNotNone(image.id)

    @unittest.skipIf(platform.architecture()[0] == "32bit", "no 32-bit image available")
    def test_pull_stream(self):
        generator = self.client.images.pull("ubi8", tag="latest", stream=True)
        self.assertIsInstance(generator, types.GeneratorType)

    @unittest.skipIf(platform.architecture()[0] == "32bit", "no 32-bit image available")
    def test_pull_stream_decode(self):
        generator = self.client.images.pull("ubi8", tag="latest", stream=True, decode=True)
        self.assertIsInstance(generator, types.GeneratorType)

    def test_scp(self):
        with self.assertRaises(APIError) as e:
            next(
                self.client.images.scp(
                    source="randuser@fake.ip.addr:22::quay.io/libpod/alpine", quiet=False
                )
            )
        self.assertRegex(
            e.exception.explanation,
            r"failed to connect: dial tcp: lookup fake\.ip\.addr.+no such host",
        )
