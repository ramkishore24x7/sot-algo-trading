source ~/.grabrc
export PATH="/usr/local/opt/go@1.18/bin:$PATH"
pax_profile() { curl -X GET \
  "https://grab-id-int.stg-myteksi.com/v1/users?phoneNumber=$1" \
  -H 'Authorization: Token PASSENGER wXXFicSx6LoxLSjTuEm8SVbJgFntlJNznyO1SVpL4ICVPzoyDkr4Dc04O9wD2hYHiDmT4ma5h8nQH7YmO6bKVL9HPlQAyKfO35tAQ3ztAveL6wQjFIp9Zi5Vy87DOajsfgpwxVo77wq6lEEln8bkL4LbMjBoMHk7iKNaBSnpsLmv962FmK5YvaSNho1WswZSZAFII1rLfFCwyqWFLD1V9xLI7fPzjZNl0MCkThlhljA6wiD7iK069UrLqQNLQqC' \
  -H 'Postman-Token: 262bf455-ae9c-4a9d-b0f3-c700f031f359' \
  -H 'cache-control: no-cache'
}

alias strikes="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/utils/strikelist.py"
alias telegram_bot="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/telegram_BOT.py; sleep 5; exit"
alias sot_bot="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/SOT_BOT.py"
alias sot_botv2="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/SOT_BOTv2.py"
alias sot_botv3="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/SOT_BOTv3.py"
alias sot_botv5="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/SOT_BOTv5.py"

alias ramflogin="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/Login_RAM.py"
alias saiflogin="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/Login_SAI.py"
alias fyers="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/Login_Fyers.py"
alias upstox="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/upstox.py"
alias FYERS="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/Login_Fyers.py"
alias ssr="fyers;upstox;telegram_bot"
alias data="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/niftytrader.py"
alias oc="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/optionchain.py"

alias banknifty_ws="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/ws_fyers_BANKNIFTY_v3.py; sleep 5; exit"
alias nifty_ws="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/ws_fyers_NIFTY_v3.py; sleep 5; exit"
alias midcpnifty_ws="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/ws_fyers_MIDCPNIFTY_v3.py; sleep 5; exit"
alias finnifty_ws="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/ws_fyers_FINNIFTY_v3.py; sleep 5; exit"
alias bajfinance_ws="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/ws_fyers_BAJFINANCE_v3.py; sleep 5; exit"
alias ws_healthcheck="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/ws_healthcheck.py; sleep 5; exit"
alias dart="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/dart.py; sleep 5; exit"
alias DART="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/dart.py; sleep 5; exit"

alias s00="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt00.py; sleep 5; exit"
alias s01="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt01.py; sleep 5; exit"
alias s02="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt02.py; sleep 5; exit"
alias s03="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt03.py; sleep 5; exit"
alias s04="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt04.py; sleep 5; exit"
alias s05="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt05.py; sleep 5; exit"
alias s10="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt10.py; sleep 5; exit"
alias s15="python3 /Users/ramkishore.gollakota/Documents/algo/Fyers/scalpAt15.py; sleep 5; exit"


pax_pin() { curl -X GET \
  "https://grab-id-int.stg-myteksi.com/v1/passengers/103292485/pin_status" \
  -H 'Authorization: Token PASSENGER wXXFicSx6LoxLSjTuEm8SVbJgFntlJNznyO1SVpL4ICVPzoyDkr4Dc04O9wD2hYHiDmT4ma5h8nQH7YmO6bKVL9HPlQAyKfO35tAQ3ztAveL6wQjFIp9Zi5Vy87DOajsfgpwxVo77wq6lEEln8bkL4LbMjBoMHk7iKNaBSnpsLmv962FmK5YvaSNho1WswZSZAFII1rLfFCwyqWFLD1V9xLI7fPzjZNl0MCkThlhljA6wiD7iK069UrLqQNLQqC' \
  -H 'Postman-Token: 262bf455-ae9c-4a9d-b0f3-c700f031f359' \
  -H 'cache-control: no-cache'
}

# bn() { 
#   if [ -z "$1" ]
#       then
#          curl -X GET "http://localhost:4001/ltp?instrument=NSE:NIFTYBANK-INDEX"
#    else
#       curl -X GET "http://localhost:4001/ltp?instrument=NSE:$1"
#    fi
# }

bn() { 
  if [ -z "$1" ]
  then
     curl -X GET "http://localhost:4001/ltp?instrument=NSE:NIFTYBANK-INDEX"
  else
     case $1 in
       NSE:*) curl -X GET "http://localhost:4001/ltp?instrument=$1";;
       *) curl -X GET "http://localhost:4001/ltp?instrument=NSE:$1";;
     esac
  fi
}

n50() {
  if [ -z "$1" ]
      then
         curl -X GET "http://localhost:4002/ltp?instrument=NSE:NIFTY50-INDEX"
   else
      case $1 in
       NSE:*) curl -X GET "http://localhost:4002/ltp?instrument=$1";;
       *) curl -X GET "http://localhost:4002/ltp?instrument=NSE:$1";;
     esac
   fi
}

midcp() {
  if [ -z "$1" ]
      then
         curl -X GET "http://localhost:4005/ltp?instrument=NSE:MIDCPNIFTY-INDEX"
   else
      case $1 in
       NSE:*) curl -X GET "http://localhost:4003/ltp?instrument=$1";;
       *) curl -X GET "http://localhost:4003/ltp?instrument=NSE:$1";;
     esac
   fi
}

fin() {
  if [ -z "$1" ]
      then
         curl -X GET "http://localhost:4003/ltp?instrument=NSE:FINNIFTY-INDEX"
   else
      case $1 in
       NSE:*) curl -X GET "http://localhost:4003/ltp?instrument=$1";;
       *) curl -X GET "http://localhost:4003/ltp?instrument=NSE:$1";;
     esac
   fi
}

baj() {
  if [ -z "$1" ]
      then
         curl -X GET "http://localhost:4004/ltp?instrument=NSE:BAJFINANCE-EQ"
   else
      case $1 in
       NSE:*) curl -X GET "http://localhost:4004/ltp?instrument=$1";;
       *) curl -X GET "http://localhost:4004/ltp?instrument=NSE:$1";;
     esac
   fi
}

time=091500
launch_banknifty_ws(){
   while (( $(date +"%Y%m%d%H%M%S") < $(date +"%Y%m%d"$time) )); do echo -ne `date` \\r ; done
   echo `date` ": Launching BankNifty Websocket..."
   banknifty_ws
}

launch_nifty_ws(){
   while (( $(date +"%Y%m%d%H%M%S") < $(date +"%Y%m%d"$time) )); do echo -ne `date` \\r ; done
   echo `date` ": Launching Nifty Websocket..."
   nifty_ws
}

launch_ws_healthcheck(){
   while (( $(date +"%Y%m%d%H%M%S") < $(date +"%Y%m%d"$time+15) )); do echo -ne `date` \\r ; done
   echo `date` ": Launching Websocket Health Checker..."
   ws_healthcheck
}

launch_telegram_botv2(){
   while (( $(date +"%Y%m%d%H%M%S") < $(date +"%Y%m%d"$time) )); do echo -ne `date` \\r ; done
   echo `date` ": Launching Telegram Bot..."
   telegram_botv2
}

launch_telegram_botv3(){
   while (( $(date +"%Y%m%d%H%M%S") < $(date +"%Y%m%d"$time) )); do echo -ne `date` \\r ; done
   echo `date` ": Launching Telegram Bot..."
   telegram_botv3
}

# Add this function to your shell configuration file (.bashrc, .zshrc, etc.)
run_in_iterm() {
    local escaped_cmd
    escaped_cmd=$(printf %q "$1")  # Escape the command passed as argument
    
    osascript -e 'tell application "iTerm" to activate' \
              -e 'tell application "System Events" to tell process "iTerm" to keystroke "D" using command down' \
              -e 'delay 3' \
              -e "tell application \"System Events\" to tell process \"iTerm\" to keystroke \"${escaped_cmd}\"" \
              -e 'tell application "System Events" to tell process "iTerm" to key code 52'
}



ZSH_THEME="simple"
source /Users/ramkishore.gollakota/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
