# Linuxbrew
if [[ $(uname -s) == "Linux" ]]; then
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
fi

# Aliases
if [[ $+commands[batcat] == 1 ]]; then
  alias cat="batcat"
elif [[ $+commands[bat] == 1 ]]; then
  alias cat="bat"
fi

if [[ $+commands[eza] == 1 ]]; then
  alias ls="eza --group-directories-first"
  alias l="eza -lah --group-directories-first"
fi

# Options
setopt autocd

# source "$HOME/src/dotfiles/zsh/themes/my.zsh-theme" # my custom basic prompt
if [[ $+commands[starship] == 1 ]]; then
  eval "$(starship init zsh)" # starship prompt https://starship.rs
fi

# Keybindings
bindkey "^[[1;5C" forward-word
bindkey "^[[1;5D" backward-word
bindkey "^H" backward-kill-word

# Fast Node Manager 
if [[ $+commands[fnm] == 1 ]]; then
  eval "`fnm env`"
fi

# Turso
export PATH="$PATH:$HOME/.turso"
