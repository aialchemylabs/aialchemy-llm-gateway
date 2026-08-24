"""Runtime object imported by ``general_settings.custom_auth``."""

from aialchemy_auth.workos import WorkOSCustomAuth

# LiteLLM imports this module while loading config, so invalid or missing
# runtime settings fail gateway startup rather than the first user request.
workos_auth = WorkOSCustomAuth.from_environment()
