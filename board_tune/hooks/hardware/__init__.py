"""Boot-time hardware interfaces delegated to the selected platform."""

from board_tune.registry import Hook


class Hardware(Hook):
    title = "Hardware interfaces"
    symbol = "HARDWARE"
    order = 60

    def read(self, ctx):
        return ctx.platform.read_hardware(ctx)

    def plan(self, ctx):
        snapshot = ctx.plan.snapshot()
        ctx.platform.setup_hardware(ctx)
        if ctx.plan.changed_since(snapshot):
            ctx.plan.note("hardware interface settings take effect after a reboot; "
                          "check connected devices and GPIO assignments first")


HOOK = Hardware()
