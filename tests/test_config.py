# SPDX-License-Identifier: MIT

"""
Tests for L{megingjord.config}.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from megingjord.config import ConfigError, load_config, setup_from_config
from megingjord.streamdeck import BrightnessDial

pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web.NotAppKeyWarning")


def write_config(tmp_path: Path, text: str) -> Path:
    """
    Write a configuration file and return its path.
    """
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadConfig:
    """
    Tests for L{megingjord.config.load_config}.
    """

    def test_lazy_integration_loading(self, tmp_path: Path) -> None:
        """
        Loading a config imports no integrations; setting up a config
        with only the host starts only the streamdeck.
        """
        from megingjord.registry import _loaded_integrations

        _loaded_integrations.clear()

        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    1:
      type: brightness
""",
            )
        )
        assert _loaded_integrations == set()

        app = web.Application()
        controller = MagicMock()
        controller.deck = MagicMock()
        app["deck_controller"] = controller
        setup_from_config(app, config)
        assert _loaded_integrations == {"streamdeck"}

    def test_minimal(self, tmp_path: Path) -> None:
        """
        A minimal config loads with defaults.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  theme: dracula
  dials:
    1:
      type: brightness
""",
            )
        )
        assert config.sections["streamdeck"]["theme"] == "dracula"
        assert (
            config.sections["streamdeck"]["dials"][1]["type"] == "brightness"
        )

    def test_env_interpolation(self, tmp_path: Path, monkeypatch) -> None:
        """
        Environment variables are interpolated into the config.
        """
        monkeypatch.setenv("HA_TOKEN", "secret")
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    1:
      type: brightness
home_assistant:
  url: wss://example.test/api/websocket
  token: ${HA_TOKEN}
""",
            )
        )
        assert config.sections["home_assistant"]["token"] == "secret"

    def test_missing_env_var(self, tmp_path: Path, monkeypatch) -> None:
        """
        A missing environment variable fails with a clear error.
        """
        monkeypatch.delenv("HA_TOKEN", raising=False)
        with pytest.raises(ConfigError, match="HA_TOKEN"):
            load_config(
                write_config(
                    tmp_path,
                    """
streamdeck:
  dials:
    1:
      type: brightness
home_assistant:
  url: wss://example.test/api/websocket
  token: ${HA_TOKEN}
""",
                )
            )

    def test_duplicate_yaml_key(self, tmp_path: Path) -> None:
        """
        Duplicate mapping keys are rejected.
        """
        with pytest.raises(ConfigError, match="Duplicate key"):
            load_config(
                write_config(
                    tmp_path,
                    """
streamdeck:
  dials:
    1:
      type: brightness
    1:
      type: brightness
""",
                )
            )

    def test_phases_may_share_keys(self, tmp_path: Path) -> None:
        """
        Google Meet phases are alternatives and may share key numbers.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    1:
      type: brightness
google_meet:
  phases:
    lobby:
      6: start-next
    greenRoom:
      6: home
""",
            )
        )
        phases = config.sections["google_meet"]["phases"]
        assert phases["lobby"][6] == "start-next"
        assert phases["greenRoom"][6] == "home"

    def test_unknown_section(self, tmp_path: Path) -> None:
        """
        An unknown configuration section is rejected.
        """
        with pytest.raises(ConfigError, match="Unknown configuration section"):
            load_config(
                write_config(
                    tmp_path,
                    """
streamdeck:
  dials:
    1:
      type: brightness
bogus:
  foo: bar
""",
                )
            )

    def test_registered_types(self, tmp_path: Path) -> None:
        """
        Setting up a config starts the integrations, which register
        their dial and key types.
        """
        from megingjord.registry import DIAL_TYPES, KEY_TYPES, SECTION_HANDLERS

        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    0:
      type: pulseaudio.sink
    1:
      type: brightness
  keys:
    0:
      type: ha.entity
      entity_id: light.test
home_assistant:
  url: wss://example.test/api/websocket
  token: secret
pulseaudio:
  output_weights: []
  input_weights: []
google_meet:
  phases:
    lobby:
      6: start-next
""",
            )
        )
        app = web.Application()
        controller = MagicMock()
        controller.deck = MagicMock()
        app["deck_controller"] = controller
        setup_from_config(app, config)

        assert "brightness" in DIAL_TYPES
        assert "pulseaudio.sink" in DIAL_TYPES
        assert "pulseaudio.source" in DIAL_TYPES
        assert "ha.entity" in DIAL_TYPES
        assert "ha.alarm" in DIAL_TYPES
        assert "ha.entity" in KEY_TYPES
        assert "ha.alarm" in KEY_TYPES
        assert "pulseaudio.sink" in KEY_TYPES
        assert "home_assistant" in SECTION_HANDLERS
        assert "pulseaudio" in SECTION_HANDLERS
        assert "google_meet" in SECTION_HANDLERS


class TestExampleConfig:
    """
    Tests for the committed example configuration.
    """

    def test_example_loads(self, tmp_path: Path, monkeypatch) -> None:
        """
        The example config loads with placeholder secrets.
        """
        monkeypatch.setenv("HA_TOKEN", "example")
        example = Path(__file__).parent.parent / "config.example.yaml"
        config = load_config(example)
        assert config.sections["streamdeck"]["theme"] == "dracula"
        assert "home_assistant" in config.sections
        assert "greenRoomSwitch" in config.sections["google_meet"]["phases"]


class TestSetupFromConfig:
    """
    Tests for L{megingjord.config.setup_from_config}.
    """

    def make_app(self) -> web.Application:
        """
        An app with a mocked deck controller.
        """
        app = web.Application()
        controller = MagicMock()
        controller.deck = MagicMock()
        app["deck_controller"] = controller
        return app

    def test_unknown_integration(self, tmp_path: Path) -> None:
        """
        A type with an unknown namespace is rejected at setup.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    1:
      type: bogus.entity
""",
            )
        )
        with pytest.raises(ConfigError, match="Unknown integration"):
            setup_from_config(self.make_app(), config)

    def test_ha_entity_requires_home_assistant(self, tmp_path: Path) -> None:
        """
        An HA entity key requires a home_assistant section.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  keys:
    0:
      type: ha.entity
      entity_id: light.test
""",
            )
        )
        with pytest.raises(ConfigError, match="home_assistant"):
            setup_from_config(self.make_app(), config)

    def test_ha_entity_requires_entity_id(self, tmp_path: Path) -> None:
        """
        An HA entity key requires an entity_id.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  keys:
    0:
      type: ha.entity
home_assistant:
  url: wss://example.test/api/websocket
  token: secret
""",
            )
        )
        with pytest.raises(ConfigError, match="entity_id"):
            setup_from_config(self.make_app(), config)

    def test_duplicate_cross_section(self, tmp_path: Path) -> None:
        """
        A key claimed by both keys and a Google Meet phase is rejected.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  keys:
    4:
      type: ha.entity
      entity_id: light.test
google_meet:
  phases:
    meeting:
      4: mic
""",
            )
        )
        with pytest.raises(ConfigError, match="claimed by both"):
            setup_from_config(self.make_app(), config)

    def test_missing_dial_type(self, tmp_path: Path) -> None:
        """
        A dial without a type is rejected with the config path.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    0: {}
""",
            )
        )
        with pytest.raises(ConfigError, match=r"dials\[0\]"):
            setup_from_config(self.make_app(), config)

    def test_unknown_home_assistant_field(self, tmp_path: Path) -> None:
        """
        An unknown home_assistant field is rejected with the config
        path.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  keys:
    0:
      type: ha.entity
      entity_id: light.test
home_assistant:
  url: wss://example.test/api/websocket
  token: secret
  bogus_field: x
""",
            )
        )
        with pytest.raises(ConfigError, match="home_assistant"):
            setup_from_config(self.make_app(), config)

    def test_google_meet_requires_phases(self, tmp_path: Path) -> None:
        """
        A google_meet section without phases is rejected.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  keys:
    0:
      type: ha.entity
      entity_id: light.test
google_meet:
  foo: bar
""",
            )
        )
        with pytest.raises(ConfigError, match="missing 'phases'"):
            setup_from_config(self.make_app(), config)

    def test_unknown_theme(self, tmp_path: Path) -> None:
        """
        An unknown theme is rejected.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  theme: nonexistent
""",
            )
        )
        with pytest.raises(ConfigError, match="unknown theme"):
            setup_from_config(self.make_app(), config)

    def test_dials_must_be_mapping(self, tmp_path: Path) -> None:
        """
        A non-mapping dials section is rejected.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    - brightness
""",
            )
        )
        with pytest.raises(ConfigError, match="'dials' must be a mapping"):
            setup_from_config(self.make_app(), config)

    def test_unknown_dial_type(self, tmp_path: Path) -> None:
        """
        An unknown dial type is rejected.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  dials:
    1:
      type: bogus
""",
            )
        )
        with pytest.raises(ConfigError, match="unknown type"):
            setup_from_config(self.make_app(), config)

    def test_builds_components(self, tmp_path: Path) -> None:
        """
        The setup registers the configured dials and keys.
        """
        config = load_config(
            write_config(
                tmp_path,
                """
streamdeck:
  theme: dracula
  dials:
    0:
      type: pulseaudio.sink
    1:
      type: brightness
    2:
      type: ha.entity
      entity_id: light.test
  keys:
    0:
      type: ha.alarm
      entity_id: alarm_control_panel.home
      arm_service: alarm_arm_night
    1:
      type: ha.entity
      entity_id: light.test
home_assistant:
  url: wss://example.test/api/websocket
  token: secret
pulseaudio:
  output_weights: []
  input_weights: []
google_meet:
  phases:
    lobby:
      6: start-next
""",
            )
        )
        app = self.make_app()
        setup_from_config(app, config)
        controller = app["deck_controller"]
        assert app["color_theme"] == "dracula"
        assert controller.register_dial.call_count == 3
        assert controller.register_key.call_count == 2
        from megingjord.pulseaudio import PulseDefaultSinkDial

        dials = [
            call.args[0] for call in controller.register_dial.call_args_list
        ]
        assert isinstance(dials[0], PulseDefaultSinkDial)
        assert isinstance(dials[1], BrightnessDial)
