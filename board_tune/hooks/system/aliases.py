"""Managed interactive Bash defaults in Debian's system startup file."""

from board_tune.errors import Error

PATH = "/etc/bash.bashrc"
BEGIN = "# >>> board-tune: ll-alias >>>"
END = "# <<< board-tune: ll-alias <<<"
BLOCK = f"""
{BEGIN}
case $- in
    *i*)
        if [ -n "${{BASH_VERSION-}}" ] && ! type ll >/dev/null 2>&1; then
            alias ll='ls -la'
        fi
        ;;
esac
{END}
"""


def _without_block(text):
    if BEGIN not in text and END not in text:
        return text
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.count(BLOCK) != 1:
        raise Error(f"{PATH}: ll-alias block was modified or is malformed; "
                    "resolve it manually or select Keep current")
    before, _, after = text.partition(BLOCK)
    return before + after


def read(plan):
    text = plan.read_text(PATH) or ""
    try:
        if _without_block(text) != text:
            return {"SYSTEM_ALIAS_LL_ENABLE": "y"}
    except Error as error:
        plan.note(str(error))
    return {}


def configure(ctx):
    cfg, plan = ctx.config, ctx.plan
    if cfg.enabled("SYSTEM_ALIAS_LL_KEEP"):
        return
    enable = cfg.enabled("SYSTEM_ALIAS_LL_ENABLE")
    if not enable and not cfg.enabled("SYSTEM_ALIAS_LL_REMOVE"):
        return
    stat = ctx.target.stat(PATH)
    if stat is None and not enable:
        return
    if stat is None or stat.kind != "file":
        raise Error(f"{PATH}: expected an existing regular Debian-style Bash startup file")
    text = plan.read_text(PATH)
    remaining = _without_block(text)
    # Existing site commands win; personal .bashrc is loaded after this file.
    if enable:
        updated = text + BLOCK if remaining == text else text
    else:
        updated = remaining
    if updated != text:
        plan.write(PATH, updated)
        plan.note("ll alias changes apply to new interactive Bash sessions; "
                  "personal shell configuration takes precedence")
