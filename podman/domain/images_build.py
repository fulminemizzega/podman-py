"""Mixin for Image build support."""

import io
import json
import logging
import pathlib
import random
import re
import shutil
import tempfile
from collections.abc import Iterator

import itertools

from podman import api
from podman.domain.images import Image
from podman.errors import BuildError, PodmanError, ImageNotFound

logger = logging.getLogger("podman.images")


class BuildMixin:
    """Class providing build method for ImagesManager."""

    # pylint: disable=too-many-locals,too-many-branches,too-few-public-methods,too-many-statements
    def build(
        self,
        path: pathlib.Path = None,
        fileobj: [pathlib.Path | io.BytesIO] = None,
        tag: str = None,
        quiet: bool = None,
        remote: str = None,
        nocache: bool = None,
        rm: bool = None,
        timeout: int = None,
        custom_context: bool = None,
        pull: bool = None,
        forcerm: bool = None,
        dockerfile: str = f".containerfile.{random.getrandbits(160):x}",
        buildargs: dict[str, str] = None,
        container_limits: dict[str, [int | str]] = None,
        shmsize: int = None,
        labels: dict[str, str] = None,
        cache_from: list[str] = None,
        target: str = None,
        network_mode: str = None,
        squash: bool = None,
        extra_hosts: dict[str, str] = None,
        platform: str = None,
        http_proxy: bool = None,
        layers: bool = True,
        output: str = None,
        outputformat: str = "application/vnd.oci.image.manifest.v1+json",
        volumes: dict[str, dict[str, [str | list[str]]]] = None,
        **kwargs,
    ) -> tuple[Image, Iterator[bytes]]:
        """Returns built image.

        Keyword Args:
            path (str) – Path to the directory containing the Dockerfile
            fileobj – A file object to use as the Dockerfile. (Or an IO object)
            tag (str) – A tag to add to the final image
            quiet (bool) – Whether to return the status
            remote (str) - A Git repository URI or HTTP/HTTPS context URI
                - If the URI is a text file, it is used as Containerfile
                - If the URI points to a tarball, the file is downloaded by the daemon and
                  its content is used as context for the build
                - If the URI points to a tarball and the dockerfile parameter is specified,
                  there must be a file with the corresponding path inside the tarball
            nocache (bool) – Don’t use the cache when set to True
            rm (bool) – Remove intermediate containers. Default True
            timeout (int) – HTTP timeout
            custom_context (bool) – Optional if using fileobj
            encoding (str) – The encoding for a stream. Set to gzip for compressing (ignored)
            pull (bool) – Downloads any updates to the FROM image in Dockerfile
            forcerm (bool) – Always remove intermediate containers, even after unsuccessful builds
            dockerfile (str) – full path to the Dockerfile / Containerfile
            buildargs (Mapping[str,str) – A dictionary of build arguments
            container_limits (dict[str, Union[int,str]]) –
                A dictionary of limits applied to each container created by the build process.
                    Valid keys:

                    - memory (int): set memory limit for build
                    - memswap (int): Total memory (memory + swap), -1 to disable swap
                    - cpushares (int): CPU shares (relative weight)
                    - cpusetcpus (str): CPUs in which to allow execution, For example, "0-3", "0,1"
                    - cpuperiod (int): CPU CFS (Completely Fair Scheduler) period (Podman only)
                    - cpuquota (int): CPU CFS (Completely Fair Scheduler) quota (Podman only)
            shmsize (int) – Size of /dev/shm in bytes. The size must be greater than 0.
                If omitted the system uses 64MB
            labels (Mapping[str,str]) – A dictionary of labels to set on the image
            cache_from (list[str]) – A list of image's identifier used for build cache resolution
            target (str) – Name of the build-stage to build in a multi-stage Dockerfile
            network_mode (str) – networking mode for the run commands during build
            squash (bool) – Squash the resulting images layers into a single layer.
            extra_hosts (dict[str,str]) – Extra hosts to add to /etc/hosts in building
                containers, as a mapping of hostname to IP address.
            platform (str) – Platform in the format os[/arch[/variant]].
            isolation (str) – Isolation technology used during build. (ignored)
            use_config_proxy (bool) – (ignored)
            http_proxy (bool) - Inject http proxy environment variables into container (Podman only)
            layers (bool) - Cache intermediate layers during build. Default True.
            output (str) - specifies if any custom build output is selected for following build.
            outputformat (str) - The format of the output image's manifest and configuration data.
                Default to "application/vnd.oci.image.manifest.v1+json" (OCI format).
            volumes (dict[str, dict[str, Union[str, list]]])
                Mount a host directory into containers when executing RUN instructions during the build.
                The key is the host path and the value is a dictionary with the keys:

                - bind: The path to mount the volume inside the container
                - mode: One or multiple of [ro|rw],[z|Z|O],[U],[[r]shared|[r]slave|[r]private]
                        By default, the volumes are mounted read-write

                For example:

                    {

                        '/etc/host':

                            {'bind': '/etc/host', 'mode': 'ro'},

                        '/tmp/cache':

                            {'bind': '/var/cache/libdnf5', 'mode': ['rw', 'z']},

                    }


        All unsupported kwargs are silently ignored.

        Returns:
            first item is the podman.domain.images.Image built

            second item is the build logs

        Raises:
            BuildError: when there is an error during the build
            APIError: when service returns an error
            TypeError: when neither path nor fileobj is not specified
        """

        if path is None and fileobj is None and remote is None:
            raise TypeError("Either path, fileobj or remote must be provided.")

        if "gzip" in kwargs and "encoding" in kwargs:
            raise PodmanError("Custom encoding not supported when gzip enabled.")

        params = {
            "dockerfile": dockerfile,
            "forcerm": forcerm,
            "httpproxy": http_proxy,
            "networkmode": network_mode,
            "nocache": nocache,
            "platform": platform,
            "pull": pull,
            "q": quiet,
            "remote": remote,
            "rm": rm,
            "shmsize": shmsize,
            "squash": squash,
            "t": tag,
            "target": target,
            "layers": layers,
            "output": output,
            "outputformat": outputformat,
        }

        if buildargs:
            params["buildargs"] = json.dumps(buildargs)
        if cache_from:
            params["cachefrom"] = json.dumps(cache_from)

        if container_limits:
            params["cpuperiod"] = container_limits.get("cpuperiod")
            params["cpuquota"] = container_limits.get("cpuquota")
            params["cpusetcpus"] = container_limits.get("cpusetcpus")
            params["cpushares"] = container_limits.get("cpushares")
            params["memory"] = container_limits.get("memory")
            params["memswap"] = container_limits.get("memswap")

        if extra_hosts:
            params["extrahosts"] = json.dumps(extra_hosts)
        if labels:
            params["labels"] = json.dumps(labels)

        if volumes:
            params["volume"] = []
            for hostdir, target in volumes.items():
                mode = target.get('mode', [])
                binddir = target.get('bind')
                if binddir is None:
                    raise ValueError(f"volume {hostdir} 'bind' value not defined")
                if not isinstance(mode, list):
                    raise ValueError(f"volume {hostdir} 'mode' value should be a list")
                mode_str = ",".join(mode)
                params["volume"].append(f"{hostdir}:{target['bind']}:{mode_str}")

        if custom_context:
            if fileobj is None:
                raise PodmanError(
                    "Custom context requires fileobj to be set to a binary file-like object containing a build-directory tarball."
                )
            if dockerfile is None:
                # TODO: Scan the tarball for either a Dockerfile or a Containerfile.
                # This could be slow if the tarball is large,
                # and could require buffering/copying the tarball if `fileobj` is not seekable.
                # As a workaround for now, don't support omitting the filename.
                raise PodmanError(
                    "Custom context requires specifying the name of the Dockerfile (typically 'Dockerfile' or 'Containerfile')."
                )
            body = io.BytesIO(fileobj.getbuffer())  # will be closed after the POST request
        elif fileobj:
            path = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
            filename = pathlib.Path(path.name) / dockerfile

            with open(filename, "w", encoding='utf-8') as file:
                shutil.copyfileobj(fileobj, file)
            body = api.create_tar(anchor=path.name, gzip=kwargs.get("gzip", False))
        elif path:
            filename = path / dockerfile
            # The Dockerfile will be copied into the context_dir if needed
            params["dockerfile"] = api.prepare_containerfile(path, str(filename))

            excludes = api.prepare_containerignore(path)
            body = api.create_tar(anchor=path, exclude=excludes, gzip=kwargs.get("gzip", False))
        elif remote:
            body = None

        post_kwargs = {}
        if timeout:
            post_kwargs["timeout"] = float(timeout)

        response = self.client.post(
            "/build",
            params=params,
            data=body,
            headers={
                "Content-type": "application/x-tar",
                # "X-Registry-Config": "TODO",
            },
            stream=True,
            **post_kwargs,
        )
        if hasattr(body, "close"):
            body.close()

        if hasattr(path, "cleanup"):
            path.cleanup()

        response.raise_for_status(not_found=ImageNotFound)

        image_id = unknown = None
        marker = re.compile(r"(^[0-9a-f]+)\n$")
        report_stream, stream = itertools.tee(response.iter_lines())
        for line in stream:
            result = json.loads(line)
            if "error" in result:
                raise BuildError(result["error"], report_stream)
            if "stream" in result:
                match = marker.match(result["stream"])
                if match:
                    image_id = match.group(1)
            unknown = line

        if image_id:
            return self.get(image_id), report_stream

        raise BuildError(unknown or "Unknown", report_stream)
