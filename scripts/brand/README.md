# Brand icon

`icon.svg` is the source of the icon in `custom_components/home_keeper_ipp/brand/`
(`icon.png` 256×256, `icon@2x.png` and `logo.png` 512×512), which Home Assistant shows
on the integration page.

## Origin

The icon was drawn from scratch for this project, as hand-written SVG from basic shapes:
a printer with a blue gradient whose body shows four cartridge levels, one of them low.
No template, icon set, stock image or other third-party artwork was used.

## Changing it

Edit `icon.svg`, then render the PNGs (Playwright with Chromium) and commit both:

```bash
cd scripts/brand
node render.js
```

The source stays outside the integration folder, so it is not part of the release ZIP.
