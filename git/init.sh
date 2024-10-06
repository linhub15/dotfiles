#!/bin/bash
git config --global user.name "Hubert Lin"
git config --global user.email "linhub15@users.noreply.github.com"

git config --global alias.l "log --color --graph --pretty=format:'%Cred%h%Creset%C(yellow)%d%Creset %s %Cgreen(%cr) %C(bold blue)<%an>%Creset' --abbrev-commit"

git config --global init.defaultBranch main
