## Font
Preferred font is DejaVuSansM

## Windows symlink to file and lua scripts
```bat
# set %HOME% environment variable before running
mklink /d "%HOME%\AppData\Local\nvim" "%HOME%\src\dotfiles\nvim"
```

## Linux & OSX symlink to file directory
```bat
ln -s ~/src/dotfiles/nvim ~/.config/nvim
```

## Functions
- `:Format`
- `:terminal`

## todo
- add whichkey prefix descriptions and more configurations
