# SPDX-License-Identifier: MIT

"""
Tests for the PulseAudio integration.
"""

from types import SimpleNamespace

from megingjord.pulseaudio import (
    PulseAudioCoordinator,
    PulseInput,
    PulseOutput,
)


def make_card(name: str = "card0", proplist: dict[str, str] | None = None):
    """
    A minimal card stub.
    """
    return SimpleNamespace(name=name, proplist=proplist or {})


def make_port(
    name: str = "analog-output",
    priority: int = 100,
    proplist: dict[str, str] | None = None,
):
    """
    A minimal port stub.
    """
    return SimpleNamespace(
        name=name, priority=priority, proplist=proplist or {}
    )


def make_coordinator(output_weights, input_weights=None):
    """
    A coordinator without a PulseAudio connection.
    """
    return PulseAudioCoordinator(
        app=SimpleNamespace(cleanup_ctx=[]),
        output_weights=output_weights,
        input_weights=input_weights or [],
    )


def test_output_weight_rule_without_card_matcher() -> None:
    """
    A weight rule matching only the port applies without crashing.

    The config handler materializes absent matchers as None via
    asdict; the matcher check must tolerate that.
    """
    output = PulseOutput(
        coordinator=make_coordinator(
            [
                {
                    "card": None,
                    "port": {"name": "analog-output"},
                    "weight": 50,
                }
            ]
        ),
        card=make_card(),
        port=make_port(name="analog-output", priority=100),
    )
    assert output.priority == 150


def test_input_weight_rule_without_card_matcher() -> None:
    """
    An input weight rule matching only the port applies without
    crashing.
    """
    input_ = PulseInput(
        coordinator=make_coordinator(
            [],
            input_weights=[{"port": {"name": "analog-input"}, "weight": 50}],
        ),
        card=make_card(),
        port=make_port(name="analog-input", priority=100),
    )
    assert input_.priority == 150


def test_weight_rule_without_matchers() -> None:
    """
    A weight rule without matchers applies to every output.
    """
    output = PulseOutput(
        coordinator=make_coordinator([{"weight": 10}]),
        card=make_card(),
        port=make_port(priority=100),
    )
    assert output.priority == 110


def test_weight_rule_with_proplist_matcher() -> None:
    """
    A weight rule matching card proplist applies only when the
    proplist matches.
    """
    weights = [
        {
            "card": {"proplist": {"device.bus": "pci"}},
            "weight": 20,
        }
    ]
    matching = PulseOutput(
        coordinator=make_coordinator(weights),
        card=make_card(proplist={"device.bus": "pci"}),
        port=make_port(priority=100),
    )
    assert matching.priority == 120

    non_matching = PulseOutput(
        coordinator=make_coordinator(weights),
        card=make_card(proplist={"device.bus": "usb"}),
        port=make_port(priority=100),
    )
    assert non_matching.priority == 100


def test_weight_rule_non_matching_port() -> None:
    """
    A weight rule for another port does not apply.
    """
    output = PulseOutput(
        coordinator=make_coordinator(
            [{"port": {"name": "headset-output"}, "weight": 50}]
        ),
        card=make_card(),
        port=make_port(name="analog-output", priority=100),
    )
    assert output.priority == 100
