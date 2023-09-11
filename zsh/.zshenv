export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

# Add Deno to Path
export PATH="$HOME/.deno/bin:$PATH"
export DENO_AUTH_TOKENS=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@raw.githubusercontent.com

# Add GoLang to Path
export PATH="$PATH:/usr/local/go/bin"

# Starship
export STARSHIP_CONFIG="$HOME/src/dotfiles/zsh/starship.toml"