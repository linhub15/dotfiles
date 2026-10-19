# Omarchy setup

- Fixed monitor scaling, workspace placement, and the boot/login screen.
- Installed Wiremix for terminal sound management.
- Switched from Bash to zsh with the dotfiles Starship prompt and Omarchy conveniences: eza, zoxide, fzf, bat previews, aliases, history, completion, and automatic directory listings.

## Zsh settings

`.zshrc` and `.zshenv` save the current Omarchy shell settings, including these
command-line navigation shortcuts:

| Shortcut | Action |
| --- | --- |
| Ctrl+Left | Move backward one word |
| Ctrl+Right | Move forward one word |
| Home | Move to the start of the command line |
| End | Move to the end of the command line |

To restore the settings:

```sh
cp ~/src/dotfiles/omarchy/.zshrc ~/.zshrc
cp ~/src/dotfiles/omarchy/.zshenv ~/.zshenv
exec zsh
```

## Monitor scaling

Keep AOC 2590G4 (DP-2) at 1x and MSI MAG 275UD E14 (DP-1) at 2x.
AOC is on the left at `0x0`; MSI is on the right at `1920x0`. Both currently use 60 Hz.
Keep workspace 1 on AOC (DP-2) and workspace 2 on MSI (DP-1), persistent across logins.
Set explicit per-monitor rules in `~/.config/hypr/monitors.lua`.

Disable the default monitor scaling hotkeys in `~/.config/hypr/bindings.lua`
to avoid accidental changes. Super+/ scales up and Super+Alt+/ scales down;
the scaling command can also rewrite the generic default scale.

```lua
hl.unbind("SUPER + SLASH")
hl.unbind("SUPER + ALT + SLASH")
```

## Default agent shortcut

Use **Super+Shift+A** (Omarchy's former ChatGPT web app shortcut) to launch the
default agent. Remove the original **Super+Ctrl+Shift+A** agent shortcut.
Add these overrides to `~/.config/hypr/bindings.lua`:

```lua
hl.unbind("SUPER + SHIFT + A")
hl.unbind("SUPER + SHIFT + CTRL + A")
o.bind("SUPER + SHIFT + A", "Agent", "omarchy-agent --pick")
```

## MEGA integration for Strata

The custom MEGAcmd provider source, icons, installer and tests live in
[`strata/providers/mega`](strata/providers/mega/README.md). It displays sync status
on configured sync-root folders, including `~/notes` synced with MEGA `/notes`,
and indexed cloud-metadata comparison badges on their files and subfolders.
Linux filesystem watches mark changed paths and ancestor folders dirty. Metadata
is cached for 24 hours, with targeted refreshes and a six-hour audit. The first
startup indexes the notes tree; normal browsing reuses saved statuses.
The private SQLite cache is `~/.cache/strata-mega/metadata.sqlite3` and stays
outside this repository.

Install or update the provider from this checkout:

```sh
python3 ~/github/dotfiles/omarchy/strata/providers/mega/install.py
```

Then fully quit and reopen Strata. Newer builds also provide
**Settings → Integrations → Refresh**; the October 13 Omarchy build has no such
settings page but does load providers at startup. The installer
generates `provider.json` with this machine's paths and copies runtime files into
`~/.config/strata/providers/mega/`. Credentials and MEGAcmd session data are not
part of this repository. MEGAcmd must be installed and running separately.
