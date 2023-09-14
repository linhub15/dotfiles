if [[ $OSTYPE == darwin* ]]; then
    export NVM_DIR="$HOME/.nvm"
    [ -s "/opt/homebrew/opt/nvm/nvm.sh" ] && \. "/opt/homebrew/opt/nvm/nvm.sh"  # This loads nvm
    [ -s "/opt/homebrew/opt/nvm/etc/bash_completion.d/nvm" ] && \. "/opt/homebrew/opt/nvm/etc/bash_completion.d/nvm"  # This loads nvm bash_completion
fi

# Add Deno to Path
export PATH="$HOME/.deno/bin:$PATH"

# Add GoLang to Path
export PATH="$PATH:/usr/local/go/bin"

# Starship
export STARSHIP_CONFIG="$HOME/src/dotfiles/zsh/starship.toml"

# Homebrew
export PATH="/opt/homebrew/bin:$PATH"
