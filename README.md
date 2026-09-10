# Megingjord

Megingjord is a set of tools for controlling devices and applications using a Stream Deck, written in Python.

It currently has support for:

- Selecting audio output devices using PulseAudio
- Toggling lights and other entities using Home Assistant
- Button actions for Google Meet (using a browser plugin)
- Clock on the Stream Deck + LCD
- Brightness of the Stream Deck + LCD display

## Installation

Install the package and its console script:

```sh
pip install .
```

## Configuration

The deck is configured with a YAML file, resolved via the XDG config
directory:

```sh
~/.config/megingjord/config.yaml
```

Start from the example and adjust it to your setup:

```sh
mkdir -p ~/.config/megingjord
cp config.example.yaml ~/.config/megingjord/config.yaml
$EDITOR ~/.config/megingjord/config.yaml
```

Secrets (the Home Assistant token) are read from environment
variables with the `${VAR}` syntax, so the config file itself contains
no secrets:

```sh
export HA_TOKEN=...
megingjord
```

A custom config path can be given with `--config`.

The log level is configured in the `logging` section (one of `debug`,
`info`, `warning`, `error`, `critical`); `--verbose` overrides it to
`debug`:
```yaml
logging:
  level: debug
```

### Running as a user service

An example systemd user unit is shipped in `packaging/megingjord.service`:

```sh
cp packaging/megingjord.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now megingjord
```

Set the secret environment variables in the unit file (or an
`EnvironmentFile`).

## Author

Ralph Meijer
<mailto:ralphm@ik.nu>
<xmpp:ralphm@ik.nu>

## Name

Megingjörð is Thor's power belt in Norse mythology
