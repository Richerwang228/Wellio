#!/bin/zsh
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd -- "${0:A:h}" || exit 1
if ! npm run ios:preview; then
  print '\nWellio 启动失败，请查看上面的提示。按回车关闭。'
  read -r
  exit 1
fi
