import asyncio
import logging
import os
import time

from .models import ServiceDefinition

_LOG = logging.getLogger("model_hub_py.process_manager")


class ProcessManager:
    async def run_shell(
        self,
        command: str,
        *,
        workdir: str | None,
        env: dict[str, str],
    ) -> int:
        merged_env = os.environ.copy()
        merged_env.update(env)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workdir,
            env=merged_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await proc.wait()

    async def start(self, service: ServiceDefinition) -> None:
        _LOG.info("Running start command for service: %s", service.name)
        code = await self.run_shell(
            service.start_command,
            workdir=service.workdir,
            env=service.env,
        )
        if code != 0:
            _LOG.error(
                "Start command failed for service: %s (exit code=%s)",
                service.name,
                code,
            )
            raise RuntimeError(f"start command failed for {service.name}: exit={code}")
        _LOG.info("Start command finished successfully for service: %s", service.name)

    async def stop(self, service: ServiceDefinition) -> None:
        _LOG.info("Running stop command for service: %s", service.name)
        code = await self.run_shell(
            service.stop_command,
            workdir=service.workdir,
            env=service.env,
        )
        if code != 0:
            _LOG.error(
                "Stop command failed for service: %s (exit code=%s)",
                service.name,
                code,
            )
            raise RuntimeError(f"stop command failed for {service.name}: exit={code}")
        _LOG.info("Stop command finished successfully for service: %s", service.name)

    async def check_healthy_once(self, service: ServiceDefinition) -> bool:
        code = await self.run_shell(
            service.healthcheck_command,
            workdir=service.workdir,
            env=service.env,
        )
        return code == 0

    async def wait_until_healthy(self, service: ServiceDefinition) -> None:
        _LOG.info("Waiting for service to become healthy: %s", service.name)
        deadline = time.monotonic() + service.startup_timeout_seconds
        while time.monotonic() < deadline:
            if await self.check_healthy_once(service):
                _LOG.info("Service passed health check: %s", service.name)
                return
            await asyncio.sleep(1)
        _LOG.error("Health check timed out for service: %s", service.name)
        raise TimeoutError(f"healthcheck timed out for {service.name}")
