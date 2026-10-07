import pytest

from board_tune.errors import Error, Unsupported
from board_tune.platforms.rpi import PLATFORM as RPI
from board_tune.platforms.rpi.configtxt import set_block
from board_tune.plan import Plan
from board_tune.registry import Context

from .conftest import CMDLINE, CONFIG_TXT


def board(id):
    return next(b for b in RPI.boards if b.id == id)


@pytest.fixture
def ctx(target):
    return Context(Plan(target), None, RPI, board("RPI5"))


def test_set_block_appends_replaces_and_removes():
    text = "arm_64bit=1\n\n[all]\n\n"
    one = set_block(text, "a", ["x=1"])
    assert one == "arm_64bit=1\n\n[all]\n\n# >>> board-tune: a >>>\nx=1\n# <<< board-tune: a <<<\n"
    two = set_block(one, "b", ["y=1"])
    assert set_block(two, "a", ["x=1"]) == two
    replaced = set_block(two, "a", ["x=2"])
    assert replaced.index("x=2") < replaced.index("y=1")
    assert set_block(set_block(one, "a", []), "b", []) == "arm_64bit=1\n\n[all]\n"
    assert set_block(text, "a", []) == text


def test_set_block_unterminated():
    with pytest.raises(Error, match="not terminated"):
        set_block("# >>> board-tune: a >>>\nx=1\n", "a", [])


def test_config_txt_block_is_board_specific(ctx, rootfs):
    RPI.config_txt(ctx, "test", ["enable_uart=1"])
    ctx.plan.apply(ctx.plan.changes())
    text = (rootfs / "boot/firmware/config.txt").read_text()
    assert text.startswith(CONFIG_TXT.rstrip("\n"))
    assert text.endswith("# >>> board-tune: test >>>\n[pi5]\nenable_uart=1\n[all]\n"
                         "# <<< board-tune: test <<<\n")


def test_old_boot_location(ctx, rootfs):
    (rootfs / "boot/firmware").rename(rootfs / "boot.old")
    (rootfs / "boot").rmdir()
    (rootfs / "boot.old").rename(rootfs / "boot")
    assert RPI.boot_dir(ctx) == "/boot"
    (rootfs / "boot/config.txt").unlink()
    with pytest.raises(Error, match="config.txt not found"):
        RPI.boot_dir(ctx)


def test_edit_kernel_cmdline(ctx, rootfs):
    RPI.edit_kernel_cmdline(ctx, lambda tokens: tokens)
    assert ctx.plan.changes() == []
    RPI.edit_kernel_cmdline(ctx, lambda tokens: [t for t in tokens if t != "quiet"])
    ctx.plan.apply(ctx.plan.changes())
    assert (rootfs / "boot/firmware/cmdline.txt").read_text() == \
        CMDLINE.replace(" quiet", "")


class FakeTarget:
    description = "fake"

    def __init__(self, model):
        self.model = model

    def read(self, path):
        assert path == "/proc/device-tree/model"
        return self.model


@pytest.mark.parametrize("model, expected", [
    (b"Raspberry Pi 4 Model B Rev 1.5\0", "RPI4"),
    (b"Raspberry Pi 400 Rev 1.1\0", "RPI4"),
    (b"Raspberry Pi 5 Model B Rev 1.0\0", "RPI5"),
    (b"Raspberry Pi Compute Module 5 Rev 1.0\0", "RPI5"),
    (b"Radxa ROCK 5B\0", None),
    (None, None),
])
def test_detect_board(model, expected):
    found = RPI.detect_board(FakeTarget(model))
    assert (found and found.id) == expected


def test_detect_unsupported_board():
    with pytest.raises(Unsupported):
        RPI.detect_board(FakeTarget(b"Raspberry Pi 3 Model B Plus Rev 1.3\0"))
