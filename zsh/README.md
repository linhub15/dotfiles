# zsh

## Ubuntu

```sh
# sudo apt install zsh

# use zsh as default
chsh -s $(which zsh)

# use bash as default
chsh -s $(which bash) 
```

```sh
ln -sf ~/src/dotfiles/zsh/.zshrc ~/.zshrc
ln -sf ~/src/dotfiles/zsh/.zshenv ~/.zshenv
```

## Dependencies

- starship
- `bat`
- `eza`