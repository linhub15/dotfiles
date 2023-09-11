export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

# Install Ruby Gems to ~/gems
export GEM_HOME="$HOME/gems"
export PATH="$HOME/gems/bin:$PATH"

# Add Deno to Path
export PATH="$HOME/.deno/bin:$PATH"
export DENO_AUTH_TOKENS=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@raw.githubusercontent.com

# Add GoLang to Path
export PATH="$PATH:/usr/local/go/bin"

# Set Ionic Capacitor path for android-studio
export CAPACITOR_ANDROID_STUDIO_PATH=/snap/bin/android-studio

# Starship
export STARSHIP_CONFIG="$HOME/src/dotfiles/zsh/starship.toml"