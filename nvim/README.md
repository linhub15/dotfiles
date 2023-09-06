## Font
Preferred font is DejaVuSansM

## Windows symlink to file and lua scripts
```bat
# set %HOME% environment variable before running
mklink /d "%HOME%\AppData\Local\nvim" "%HOME%\src\dotfiles\nvim"
```

## Linux symlink to file directory
```bat
ln -s ~/src/dotfiles/nvim ~/.config/nvim
```

## todo
- formatter (md, sh, yaml, toml, html)
- git integration
- find a color theme 
- deno integration
- node integration
- filetree (neotree)
