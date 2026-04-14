tell application "iTerm2"
    set newWindow to (create window with default profile)
    tell current session of newWindow
		delay 1
		tell application "System Events" to keystroke "f" using {command down, option down}
		delay 1
		write text "launch_banknifty_ws"
		tell application "System Events" to key code 52
		delay 1
		tell application "System Events" to keystroke "D" using {command down, shift down}
		delay 1
		write text "launch_nifty_ws"
		tell application "System Events" to key code 52
		delay 1
		tell application "System Events" to keystroke "D" using {command down, shift down}
		delay 1
		write text "launch_ws_healthcheck"
		tell application "System Events" to key code 52
		delay 1
		tell application "System Events" to keystroke "D" using {command down, shift down}
		delay 1
		write text "launch_telegram_botv3"
		tell application "System Events" to key code 52
    end tell
end tell