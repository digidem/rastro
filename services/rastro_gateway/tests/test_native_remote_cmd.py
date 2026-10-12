import pytest

from rastro_gateway.native import remote_cmd as rc

ON = {rc.ENV_ENABLED: "1"}


def mk(action="reboot", kind="boat", crew=False, node="!a35a8024"):
    return rc.parse_command(node, action, kind, crew)


def test_disabled_by_default():
    assert rc.is_enabled({}) is False
    with pytest.raises(rc.CommandError) as e:
        rc.authorize(mk(), {})
    assert e.value.code == "disabled"


@pytest.mark.parametrize("kind", ["boat", "solar", "fixed", "unknown", "xyz"])
def test_reboot_allowed_any_kind(kind):
    rc.authorize(mk("reboot", kind), ON)


@pytest.mark.parametrize("kind", ["solar", "fixed", "unknown", "xyz"])
def test_shutdown_denied_non_boat(kind):
    with pytest.raises(rc.CommandError) as e:
        rc.authorize(mk("shutdown", kind, True), ON)
    assert e.value.code == "shutdown_denied_kind"


def test_shutdown_boat_needs_crew():
    with pytest.raises(rc.CommandError) as e:
        rc.authorize(mk("shutdown", "boat", False), ON)
    assert e.value.code == "shutdown_denied_crew"
    rc.authorize(mk("shutdown", "boat", True), ON)


@pytest.mark.parametrize("a", ["factory_reset", "factory-reset", "wipe", "", "REBOOT_OTA", None, 1])
def test_other_actions_rejected(a):
    with pytest.raises(rc.CommandError) as e:
        rc.parse_command("!a35a8024", a, "boat", True)
    assert e.value.code == "bad_action"


@pytest.mark.parametrize("n", ["a35a8024", "!A35A8024", "!a35a80", "!zzzzzzzz", "!a35a8024\n", None, 5])
def test_bad_node(n):
    with pytest.raises(rc.CommandError) as e:
        rc.parse_command(n, "reboot", "boat")
    assert e.value.code == "bad_node"


@pytest.mark.parametrize("c", ["yes", 1, 0, None])
def test_crew_must_be_bool(c):
    with pytest.raises(rc.CommandError) as e:
        rc.parse_command("!a35a8024", "shutdown", "boat", c)
    assert e.value.code == "bad_crew"


@pytest.mark.parametrize("v", ["0", "true", "", "yes"])
def test_flag_only_exact_one(v):
    assert rc.is_enabled({rc.ENV_ENABLED: v}) is False


def test_flag_on_exact_one():
    assert rc.is_enabled({rc.ENV_ENABLED: "1"}) is True


def test_shutdown_denied_while_disabled():
    with pytest.raises(rc.CommandError) as e:
        rc.authorize(mk("shutdown", "boat", True), {})
    assert e.value.code == "disabled"


def test_action_case_insensitive():
    assert mk("REBOOT").action is rc.Action.REBOOT
