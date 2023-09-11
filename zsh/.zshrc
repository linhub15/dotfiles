# Aliases
if [[ $+commands[batcat] == 1 ]]; then
  alias cat="batcat"
elif [[ $+commands[bat] == 1 ]]; then
  alias cat="bat"
fi

if [[ $+commands[exa] == 1 ]]; then
  alias ls="exa --group-directories-first"
  alias l="exa -lah --group-directories-first"
fi

# Options
setopt autocd

# source "$HOME/src/dotfiles/zsh/themes/my.zsh-theme" # my custom basic prompt
eval "$(starship init zsh)" # starship prompt https://starship.rs