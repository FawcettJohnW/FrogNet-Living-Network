FrogNet CLI — meta wrapper & shell completions

This bundle installs:
- /usr/local/bin/frognet_common.sh
- /usr/local/bin/frog  (meta command wrapper)
- /usr/local/bin/{create,read,update,delete,list}_*.sh and plural list_* scripts
- /etc/bash_completion.d/frog
- /usr/local/share/zsh/site-functions/_frog
- /usr/share/fish/vendor_completions.d/frog.fish

INSTALL
  sudo unzip -o frognet_cli_bin_plus.zip -d /
  # Bash: ensure bash-completion is installed; then start a new shell or run:
  source /etc/bash_completion
  # Zsh: ensure fpath includes /usr/local/share/zsh/site-functions, then:
  autoload -U compinit && compinit
  # Fish: completions autoload from vendor dir; restart fish or run:
  fish_update_completions

USAGE
  frog <entity> <subcommand> [args...]

Entities:
  user, team, team-member, message, known-frognet, sensor, iahost, actuator, well-known-site, sensor-data

Subcommands:
  create, get, list, update, delete, list-all
  (messages) inbox <CallSign> [limit], outbox <CallSign> [limit]

Examples:
  export BASE_URL="http://databasehost.frognet/api.php"
  frog user create K7JWF "John W Fawcett" "secret"
  frog sensor create FROG-123 10.0.1.50 10.0.1.0/24 TempProbe-1 DS18B20 "lab,thermal"
  frog sensor get 7
  frog sensor list FrogID=FROG-123 Tags__like=%thermal% limit=10
  frog messages inbox K1ABC 20
