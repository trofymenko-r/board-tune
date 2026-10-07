import textwrap

import pytest

from board_tune.errors import Error
from board_tune.kconfig import Configuration
from board_tune.registry import Registry


@pytest.fixture
def registry(ext_path):
    return Registry.load()


@pytest.fixture
def conf(registry, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return Configuration(registry, str(work))


def fragment(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return str(path)


def test_external_hooks_and_platforms_are_discovered(registry):
    assert "dummy" in [p.name for p in registry.platforms]
    assert "greeting" in [h.name for h in registry.hooks]


def test_defaults(conf, registry):
    assert not conf.config.enabled("GREETING")
    assert conf.config.enabled("BOARD_" + registry.platforms[0].boards[0].id)
    assert conf.problems() == []


def test_fragments_merge_in_order(conf, tmp_path):
    conf.load(fragment(tmp_path, "a", """\
        CONFIG_BOARD_DUMMY_B=y
        CONFIG_GREETING=y
        CONFIG_GREETING_TEXT="first"
    """))
    conf.load(fragment(tmp_path, "b", """\
        CONFIG_GREETING_TEXT="second"
    """))
    assert conf.config.enabled("BOARD_DUMMY_B")
    assert conf.config.str("GREETING_TEXT") == "second"
    assert conf.problems() == []


def test_set(conf):
    conf.set("GREETING=y")
    conf.set("CONFIG_GREETING_TEXT=hi there")
    conf.set("GREETING_REPEAT=3")
    assert conf.config.str("GREETING_TEXT") == "hi there"
    assert conf.config.int("GREETING_REPEAT") == 3
    with pytest.raises(Error):
        conf.set("NO_SUCH_SYMBOL=y")
    with pytest.raises(Error):
        conf.set("GREETING")


def test_unknown_symbols_are_reported(conf, tmp_path):
    conf.load(fragment(tmp_path, "a", "CONFIG_NO_SUCH_SYMBOL=y\n"))
    [problem] = conf.problems()
    assert "NO_SUCH_SYMBOL" in problem


def test_unmet_dependencies_are_reported(conf, tmp_path):
    conf.load(fragment(tmp_path, "a", """\
        CONFIG_BOARD_DUMMY_A=y
        CONFIG_GREETING=y
        CONFIG_DUMMY_B_LOUD=y
    """))
    [problem] = conf.problems()
    assert problem.startswith("DUMMY_B_LOUD=y is not possible")
    assert "BOARD_DUMMY_B" in problem


def test_minimal_config_roundtrip(conf, registry, tmp_path):
    conf.set("BOARD_DUMMY_B=y")
    conf.set("GREETING=y")
    conf.set("DUMMY_B_LOUD=y")
    saved = tmp_path / "saved.cfg"
    conf.save(str(saved))
    text = saved.read_text()
    assert "CONFIG_GREETING_TEXT" not in text
    assert "CONFIG_GREETING=y" in text

    work = tmp_path / "work2"
    work.mkdir()
    again = Configuration(registry, str(work))
    again.load(str(saved))
    assert again.problems() == []
    assert again.config.enabled("BOARD_DUMMY_B")
    assert again.config.enabled("DUMMY_B_LOUD")
