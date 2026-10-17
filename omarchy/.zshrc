# Omarchy environment and user tool paths.
[[ -r /usr/share/omarchy/default/bash/env-bootstrap ]] && source /usr/share/omarchy/default/bash/env-bootstrap

# Runtime versions managed by mise.
if (( $+commands[mise] )); then
  eval "$(mise activate zsh)"
fi

# Use the prompt maintained in your dotfiles repository.
export STARSHIP_CONFIG="$HOME/src/dotfiles/zsh/starship.toml"
if (( $+commands[starship] )); then
  eval "$(starship init zsh)"
fi

# Enter directories by name and list contents after changing directories.
setopt autocd
autoload -Uz add-zsh-hook
add-zsh-hook chpwd list_directory_contents

# Omarchy CLI defaults and aliases (these files use syntax supported by zsh).
source "$OMARCHY_PATH/default/bash/envs"
source "$OMARCHY_PATH/default/bash/aliases"

# zsh keeps multiword editor commands as one value; split them explicitly.
unalias eff 2>/dev/null
function eff() {
  local selected
  selected=$(ff) && [[ -n $selected ]] && ${=EDITOR} "$selected"
}

# Persistent history and native zsh completion.
HISTFILE="$HOME/.zsh_history"
HISTSIZE=32768
SAVEHIST=32768
setopt appendhistory sharehistory histignoredups histignorespace
unsetopt hashcmds
autoload -Uz compinit
compinit
bindkey -e

# Move one word with Ctrl+Left / Ctrl+Right.
bindkey '^[[1;5D' backward-word
bindkey '^[[1;5C' forward-word

# Move to the start/end of the command line with Home / End.
bindkey '^[[H' beginning-of-line
bindkey '^[[F' end-of-line
bindkey '^[OH' beginning-of-line
bindkey '^[OF' end-of-line
bindkey '^[[1~' beginning-of-line
bindkey '^[[4~' end-of-line
bindkey '^[[7~' beginning-of-line
bindkey '^[[8~' end-of-line

# Delete the character under the cursor with Delete.
bindkey '^[[3~' delete-char

if (( $+commands[zoxide] )); then
  eval "$(zoxide init zsh)"
fi

if (( $+commands[fzf] )); then
  [[ -r /usr/share/fzf/completion.zsh ]] && source /usr/share/fzf/completion.zsh
  [[ -r /usr/share/fzf/key-bindings.zsh ]] && source /usr/share/fzf/key-bindings.zsh
fi

if (( $+commands[try] )); then
  eval "$(SHELL=/usr/bin/zsh command try init "$HOME/Work/tries")"
fi

# Match Omarchy's ls alias when listing after a directory change.
function list_directory_contents() {
  if (( $+commands[eza] )); then
    command eza -lh --group-directories-first --icons=auto
  else
    command ls
  fi
}

# pnpm global executables.
export PNPM_HOME="$HOME/.local/share/pnpm"
case ":$PATH:" in
  *":$PNPM_HOME/bin:"*) ;;
  *) export PATH="$PNPM_HOME/bin:$PATH" ;;
esac
