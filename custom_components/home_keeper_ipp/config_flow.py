"""Config and options flow for the IPP glue.

One instance watches every IPP printer, so setup is a single confirmation. The
behaviour lives in the options.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    DEFAULT_CLEAR_ON_REFILL,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_RECOVERY_MARGIN,
    DEFAULT_STOCK_ENABLED,
    DEFAULT_THRESHOLD,
    DOMAIN,
    IPP_DOMAIN,
    MANAGED_DISPLAY_NAME,
    OPT_CLEAR_ON_REFILL,
    OPT_EXCLUDED_ENTITIES,
    OPT_NAME_TEMPLATE,
    OPT_RECOVERY_MARGIN,
    OPT_STOCK_ENABLED,
    OPT_THRESHOLD,
)


class IppGlueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm the single instance."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=MANAGED_DISPLAY_NAME, data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return IppGlueOptionsFlow()


def _percent(min_value: int, max_value: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=min_value,
            max=max_value,
            step=1,
            unit_of_measurement="%",
            mode=NumberSelectorMode.BOX,
        )
    )


class IppGlueOptionsFlow(OptionsFlow):
    """Threshold, refill detection, name template, exclusions and spare stock."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and save the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[OPT_THRESHOLD]) + float(user_input[OPT_RECOVERY_MARGIN]) > 100:
                errors["base"] = "limits_too_high"
            else:
                user_input[OPT_NAME_TEMPLATE] = (user_input.get(OPT_NAME_TEMPLATE) or "").strip()
                return self.async_create_entry(title="", data=user_input)

        opts = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_THRESHOLD, default=opts.get(OPT_THRESHOLD, DEFAULT_THRESHOLD)
                ): _percent(1, 95),
                vol.Required(
                    OPT_RECOVERY_MARGIN,
                    default=opts.get(OPT_RECOVERY_MARGIN, DEFAULT_RECOVERY_MARGIN),
                ): _percent(1, 99),
                vol.Required(
                    OPT_CLEAR_ON_REFILL,
                    default=opts.get(OPT_CLEAR_ON_REFILL, DEFAULT_CLEAR_ON_REFILL),
                ): bool,
                vol.Optional(
                    OPT_NAME_TEMPLATE,
                    description={
                        "suggested_value": opts.get(OPT_NAME_TEMPLATE, DEFAULT_NAME_TEMPLATE)
                    },
                ): TextSelector(),
                vol.Optional(
                    OPT_EXCLUDED_ENTITIES,
                    default=list(opts.get(OPT_EXCLUDED_ENTITIES) or []),
                ): EntitySelector(
                    EntitySelectorConfig(integration=IPP_DOMAIN, domain="sensor", multiple=True)
                ),
                vol.Required(
                    OPT_STOCK_ENABLED,
                    default=opts.get(OPT_STOCK_ENABLED, DEFAULT_STOCK_ENABLED),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            # The template descriptions name the placeholders to type; give them as
            # literal values so Home Assistant's message formatter leaves them alone.
            description_placeholders={"marker": "{marker}", "printer": "{printer}"},
        )
