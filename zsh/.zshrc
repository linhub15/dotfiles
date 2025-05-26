# Linuxbrew
if [[ $(uname -s) == "Linux" ]]; then
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
fi

# Aliases
alias c="code"

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

# asdf - runtime manager
export PATH="${ASDF_DATA_DIR:-$HOME/.asdf}/shims:$PATH"
## asdf - append completions to fpath
fpath=(${ASDF_DATA_DIR:-$HOME/.asdf}/completions $fpath)
## asdf - initialise completions with ZSH's compinit
autoload -Uz compinit && compinit
