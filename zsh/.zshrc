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
eval "$(starship init zsh)" # starship prompt https://starship.rs

# Fast Node Manager 
if [[ $+commands[fnm] == 1 ]]; then
  eval "`fnm env`"
  alias nvm="fnm"
fi

# Keybindings
bindkey "^[[1;5C" forward-word
bindkey "^[[1;5D" backward-word
bindkey "^H" backward-kill-word

# pnpm
export PNPM_HOME="/home/linhub/.local/share/pnpm"
case ":$PATH:" in
  *":$PNPM_HOME:"*) ;;
  *) export PATH="$PNPM_HOME:$PATH" ;;
esac
# pnpm end

# bun completions
if [[ $+commands[bun] == 1 ]]; then
  [ -s "/home/linhub/.bun/_bun" ] && source "/home/linhub/.bun/_bun"
fi
