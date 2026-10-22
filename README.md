# Home Keeper — IPP printer supplies

[![Validate](https://github.com/financelurker/ha-home-keeper-ipp/actions/workflows/validate.yml/badge.svg)](https://github.com/financelurker/ha-home-keeper-ipp/actions/workflows/validate.yml)

Turns the ink and toner levels of Home Assistant's built-in [IPP](https://www.home-assistant.io/integrations/ipp/) integration into [Home Keeper](https://github.com/prestomation/ha-home-keeper) tasks: one *"Replace Black ink (HP OfficeJet)"* task per cartridge, raised when the cartridge runs low and cleared when the printer reports a new one.

It is a small *glue* integration in the sense of Home Keeper's [glue pattern](https://github.com/prestomation/ha-home-keeper/blob/main/docs/GLUE_INTEGRATIONS.md): it changes neither IPP nor Home Keeper and talks to both only through the registries, entity states, services and events.

- **One task per cartridge:** a triggered task on the printer's device, with the cartridge type as a chip and the level that raised it in the notes.
- **The printer's own low level:** each cartridge uses the *marker low level* its printer reports, with a fixed fallback for printers that report none.
- **Cleared by the printer:** a new cartridge completes the task, so its history shows how long your cartridges last. A manual *Done* while the printer still reports low is undone.
- **Spare cartridges (optional):** one appliance per printer with every cartridge as a consumable part; each replacement takes one off the stock, and Home Keeper can create a buy task when you run low.
- **Disabled printers:** a disabled printer, device or sensor pauses its tasks instead of deleting them.

## Installation

Requires Home Assistant 2026.10 or newer, the IPP integration with at least one printer, and Home Keeper.

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=financelurker&repository=ha-home-keeper-ipp&category=integration)

1. Click the button above. It opens the repository in HACS and adds it as a custom repository if needed.
2. Select **Download** and restart Home Assistant.

Without the button: HACS → ⋮ → **Custom repositories** → add `https://github.com/financelurker/ha-home-keeper-ipp` as type *Integration*.

### Manual

1. Copy `custom_components/home_keeper_ipp` into `config/custom_components/`.
2. Restart Home Assistant.

A manual copy can be switched to HACS later: download it in HACS over the existing folder and restart. Tasks, history and spare counts stay as they are.

## Setup

**Settings → Devices & services → Add integration → Home Keeper — IPP printer supplies.** One instance covers every IPP printer. It then shows up in Home Keeper under **Settings → Companions**.

| Option | Default | Meaning |
|---|---|---|
| Fallback low limit | 15 % | Used for a cartridge whose printer reports no low level (0 or nothing). |
| Refill margin | 20 points | A new cartridge is recognised once the level is this far above the cartridge's low limit. |
| Clear the task when a new cartridge is reported | on | Off: you clear the task yourself, and a manual *Done* stands. |
| Task name template | language default | Placeholders `{marker}` (cartridge) and `{printer}`. Applies to new tasks. |
| Cartridges to ignore | none | Their tasks are removed, for example for a waste-toner box or a drum. |
| Count spare cartridges | on | The spare-cartridge appliance per printer, see below. |

## How it decides

For each cartridge (IPP *marker*), the low limit is the printer's *marker low level*, or the fallback when the printer reports none. The task notes say which one applied.

Each task is bound to the cartridge by the name the printer reports for it, not by the sensor. The IPP integration numbers its sensors by their position in the printer's marker list sorted by name, so a marker the printer adds, drops or renames (after a firmware update, for example) shifts the numbers. The task then moves to the sensor that carries its cartridge now, with its history and its spare part. A cartridge the printer no longer reports under its name loses its task. Renaming a sensor in Home Assistant changes nothing.

| The printer reports … | Result in Home Keeper |
|---|---|
| level at or below the low limit | the task is created or armed (also right after setup) |
| level between the low limit and low limit + margin | nothing; a level wobbling near the limit stays quiet |
| level at or above low limit + margin | the task is completed: a new cartridge went in |
| the cartridge runs low again | the same task is armed again, its history kept |
| you mark the task done, the printer still reports low | the completion is undone and the task stays due |
| unknown level, printer offline | nothing |

## Home Keeper

- **Tasks** are *triggered* tasks on the printer's device, managed by this integration (*Managed by IPP printer*). Their name and device are locked; deleting them in Home Keeper is blocked while this integration is installed.
- **Spare cartridges:** one appliance per printer, named after it, with every cartridge as a *consumable* part, linked to that cartridge's task. Enter the stock and a reorder point on a part and turn on *Auto-create buy task* to get a buy task when you run low. The part list follows the printer; stock numbers are yours.
- **Disabled printer, device or sensor:** its tasks are switched off (not deleted; out of the to-do list and calendar). When it is enabled again they are switched on and caught up with the current level. A task you switched off yourself is never touched.
- **Deleted printer:** its tasks are deleted. A spare-cartridge appliance with counted stock stays.
- **Home Keeper starting or reloading:** nothing is lost. The integration waits until Home Keeper is loaded and then catches up once.
- **Removing this integration** deletes its tasks and releases its appliances: those with counted stock stay as ordinary appliances.

## Releases

The GitHub release tag is the only source of the version; nothing in the repository holds it (`manifest.json` keeps the placeholder `0.0.0`, which Home Assistant needs to load the integration).

1. Create a release on GitHub with a tag such as `v1.2.3`, on a commit that contains the *Release* workflow. Pre-releases get their ZIP the same way; HACS offers them only with **Show beta versions** turned on.
2. The *Release* workflow takes the version from the tag (without the leading `v`; it stops with an error if the tag isn't a version number), writes it into `manifest.json`, packs the integration as `home_keeper_ipp.zip` and attaches it to the release.
3. HACS installs that ZIP (`zip_release` in `hacs.json`), so Home Assistant shows the release version.

To rebuild the ZIP of an existing release, run the *Release* workflow under **Actions → Release → Run workflow** with the release's tag.

Good to know:

- HACS installs **only from releases** (their ZIP). As long as there is no full release (only pre-releases or none), HACS installs fail.
- The ZIP is attached about a minute after the release is published. An update started in that minute fails; just try again.
- The workflow stops if the tag isn't a version Home Assistant accepts, so a release can't ship an integration that Home Assistant refuses to load.

## Development

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

The tests run in a real Home Assistant test instance (`pytest-homeassistant-custom-component`), against Home Keeper's test fake and the real Home Keeper. They need Home Keeper in `custom_components/home_keeper`; CI copies a pinned release there (see `.github/workflows/validate.yml`), locally those tests are skipped without it. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT License, see [LICENSE](LICENSE).

This project is not affiliated with Home Assistant, the Open Home Foundation or Home Keeper.
