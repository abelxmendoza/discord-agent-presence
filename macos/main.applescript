-- Agent Presence.app: a double-click installer, updater and uninstaller.
-- Built into an applet by macos/build_app.sh. The real work is in
-- Contents/Resources/install_core.sh.

property appTitle : "Agent Presence"
property webChoice : "Show it on my GitHub profile and website"
property linkChoice : "Link my other computers"
property uninstallChoice : "Uninstall"

on run
	set core to quoted form of (POSIX path of (path to me) & "Contents/Resources/install_core.sh")
	set ver to do shell script core & " version"
	set curState to do shell script core & " status"
	set myIcon to path to resource "applet.icns"

	if curState is "not-installed" then
		set msg to "Show what your AI coding agents are doing on your Discord profile." & return & return & ¬
			"Works with Claude, Codex, Gemini, Copilot, Cursor, VS Code and more. It starts by itself each time you log in, and no code ever leaves your Mac."
		display dialog msg with title appTitle & " " & ver buttons {"Cancel", "Install"} default button "Install" cancel button "Cancel" with icon myIcon
		my doInstall(core, myIcon, "installed")
	else
		set AppleScript's text item delimiters to " "
		set parts to text items of curState
		set AppleScript's text item delimiters to ""
		set installedVer to "?"
		if (count of parts) > 1 then set installedVer to item 2 of parts
		if item 1 of parts is "running" then
			set stateText to "is running"
		else
			set stateText to "is installed but not running"
		end if
		if installedVer is ver then
			set mainButton to "Restart"
		else
			set mainButton to "Update to " & ver
		end if
		set r to display dialog appTitle & " " & installedVer & " " & stateText & "." & return & return & ¬
			"Your card appears on your Discord profile whenever an AI tool is open." with title appTitle buttons {"More…", "Done", mainButton} default button mainButton cancel button "Done" with icon myIcon
		if button returned of r is "More…" then
			my moreMenu(core, myIcon)
		else
			my doInstall(core, myIcon, "updated")
		end if
	end if
end run

on doInstall(core, myIcon, verb)
	set progress total steps to 3
	set progress description to "Installing " & appTitle & "…"

	set progress additional description to "Copying files"
	do shell script core & " files"
	set progress completed steps to 1

	set progress additional description to "Setting up (about 20 seconds the first time)"
	repeat
		try
			do shell script core & " runtime"
			exit repeat
		on error errMsg number errNum
			if errNum is 3 then
				my askForTools(myIcon)
			else
				my failed(errMsg, myIcon)
			end if
		end try
	end repeat
	set progress completed steps to 2

	set progress additional description to "Starting it at login"
	try
		do shell script core & " service"
	on error errMsg
		my failed(errMsg, myIcon)
	end try
	set progress completed steps to 3

	my finish(core, myIcon, verb)
end doInstall

-- No Python 3.9+ on this Mac: Apple's free Command Line Tools include one.
on askForTools(myIcon)
	set msg to "Agent Presence needs Python, which comes with Apple's free Command Line Tools." & return & return & ¬
		"Click Install Tools, then click Install in Apple's window. When it says the software was installed, come back here and click Continue."
	display dialog msg with title appTitle buttons {"Cancel", "Install Tools"} default button "Install Tools" cancel button "Cancel" with icon myIcon
	try
		do shell script "xcode-select --install"
	end try
	display dialog "Waiting for Apple's installer to finish (usually 5–10 minutes)." & return & return & "Click Continue once it's done." with title appTitle buttons {"Cancel", "Continue"} default button "Continue" cancel button "Cancel" with icon myIcon
end askForTools

on finish(core, myIcon, verb)
	set discordApp to ""
	repeat with p in {"/Applications/Discord.app", POSIX path of (path to home folder) & "Applications/Discord.app"}
		if (do shell script "test -d " & quoted form of p & " && echo yes || echo no") is "yes" then set discordApp to p as text
	end repeat

	set msg to "Agent Presence is " & verb & " and running." & return & return & ¬
		"Open an AI tool and your card shows up on your Discord profile within a few seconds." & return & return & ¬
		"Optional: allow window titles so projects open in Cursor or VS Code show up too."
	if discordApp is "" then
		set discordButton to "Get Discord"
		set msg to msg & return & return & "Discord's desktop app isn't installed yet. Cards need it running."
	else
		set discordButton to "Open Discord"
	end if
	set r to display dialog msg with title appTitle buttons {"Allow Window Titles…", discordButton, "Done"} default button "Done" with icon myIcon
	set b to button returned of r
	if b is "Get Discord" then
		open location "https://discord.com/download"
	else if b is "Open Discord" then
		do shell script "open " & quoted form of discordApp
	else if b is "Allow Window Titles…" then
		my allowTitles(core, myIcon)
	end if
end finish

on allowTitles(core, myIcon)
	set py to ""
	try
		set py to do shell script core & " python-path"
	end try
	if py is not "" then set the clipboard to py
	set msg to "In the window that opens:" & return & return & ¬
		"1. Click + at the bottom of the list (enter your password if asked)." & return & ¬
		"2. Press ⌘⇧G, paste with ⌘V, and press Return." & return & ¬
		"3. Click Open and make sure the new Python switch is on." & return & return & ¬
		"The path is already copied:" & return & py
	display dialog msg with title appTitle buttons {"Open Settings"} default button "Open Settings" with icon myIcon
	open location "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
end allowTitles

on moreMenu(core, myIcon)
	set picked to choose from list {webChoice, linkChoice, uninstallChoice} with title appTitle with prompt "What would you like to do?" OK button name "Continue"
	if picked is false then return
	set picked to item 1 of picked
	if picked is webChoice then
		my setUpWeb(core, myIcon)
	else if picked is linkChoice then
		my linkComputers(core, myIcon)
	else
		my doUninstall(core, myIcon)
	end if
end moreMenu

-- Publishes status.json and status.svg to your GitHub profile repo, for the
-- README card and the website widget. Linked computers' agents are included.
on setUpWeb(core, myIcon)
	set msg to "Show what you're coding on your GitHub profile README and your website, updated every couple of minutes." & return & return & ¬
		"It keeps a status branch in your profile repo. Private repos are never named." & return & return & ¬
		"Also add the card to your profile README? (One commit; skip it if you'll add it yourself.)"
	set r to display dialog msg with title appTitle buttons {"Cancel", "Status Only", "Add to README"} default button "Add to README" cancel button "Cancel" with icon myIcon
	set extra to ""
	if button returned of r is "Add to README" then set extra to " --readme"
	set tok to my askToken(myIcon)
	if tok is not "" then
		-- Hand the token over in a file in your private temp folder, so it never
		-- shows in the process list. The tool deletes the file after reading it.
		set tokPath to POSIX path of (path to temporary items from user domain) & "agent-presence-token"
		set fh to open for access POSIX file tokPath with write permission
		set eof fh to 0
		write tok to fh as «class utf8»
		close access fh
		set extra to extra & " --token-file " & quoted form of tokPath
	end if
	try
		do shell script core & " web" & extra
	on error errMsg
		do shell script "rm -f " & quoted form of (POSIX path of (path to temporary items from user domain) & "agent-presence-token")
		my failed(errMsg & return & return & "Check that the token can write to your profile repo (Contents: Read and write), then try again.", myIcon)
	end try
	display dialog "Publishing. Your GitHub card and website widget update within a couple of minutes, with the agents from all your linked computers." with title appTitle buttons {"Done"} default button "Done" with icon myIcon
end setUpWeb

-- A fine-grained token that can only write your profile repo, or "" to use
-- the GitHub CLI login after a warning.
on askToken(myIcon)
	set msg to "Paste a GitHub token that can only write your profile repo:" & return & return & ¬
		"1. Click Get a Token (opens GitHub)." & return & ¬
		"2. Repository access: Only select repositories → your profile repo (the one named after you)." & return & ¬
		"3. Permissions: Contents → Read and write. Generate, copy, paste here."
	repeat
		set r to display dialog msg default answer "" with hidden answer with title appTitle buttons {"Cancel", "Get a Token…", "Continue"} default button "Continue" cancel button "Cancel" with icon myIcon
		if button returned of r is "Get a Token…" then
			open location "https://github.com/settings/personal-access-tokens/new"
		else
			set tok to text returned of r
			if tok is not "" then return tok
			display dialog "Use your GitHub CLI login instead? It can write to all of your repos, and the app keeps using it in the background." with title appTitle buttons {"Back", "Use CLI Login"} default button "Back" with icon caution
			if button returned of result is "Use CLI Login" then return ""
		end if
	end repeat
end askToken

on linkComputers(core, myIcon)
	set msg to "Linked computers share one Discord card and one GitHub status, showing every computer's agents. On different networks, also turn on " & quote & webChoice & quote & " on each." & return & return & ¬
		"On your first computer, get a key. On each of the others, enter it."
	set r to display dialog msg with title appTitle buttons {"Cancel", "Enter a Key", "Get a Key"} default button "Get a Key" cancel button "Cancel" with icon myIcon
	if button returned of r is "Get a Key" then
		try
			set k to do shell script core & " link-key"
		on error errMsg
			my failed(errMsg, myIcon)
		end try
		set the clipboard to k
		-- Restart so this computer starts listening with the key.
		try
			do shell script core & " service"
		on error errMsg
			my failed(errMsg, myIcon)
		end try
		display dialog "Your link key (copied):" & return & return & k & return & return & ¬
			"On another Mac: open Agent Presence → More… → " & linkChoice & " → Enter a Key." & return & ¬
			"On Linux or Windows: agent-presence link " & k & " then agent-presence setup" & return & return & ¬
			"Keep it private. If macOS asks to let Python find devices on your network, click Allow." with title appTitle buttons {"Done"} default button "Done" with icon myIcon
	else
		set k to text returned of (display dialog "Paste the link key from your first computer:" default answer "" with title appTitle buttons {"Cancel", "Link"} default button "Link" cancel button "Cancel" with icon myIcon)
		if k is "" then return
		try
			do shell script core & " link " & quoted form of k
		on error errMsg
			my failed(errMsg, myIcon)
		end try
		display dialog "Linked. This computer's agents now show on your card and GitHub status. If macOS asks to let Python find devices on your network, click Allow." with title appTitle buttons {"Done"} default button "Done" with icon myIcon
	end if
end linkComputers

on doUninstall(core, myIcon)
	display dialog "Remove Agent Presence? Your Discord card stops showing. Your settings file is kept in case you come back." with title appTitle buttons {"Cancel", "Uninstall"} default button "Cancel" cancel button "Cancel" with icon caution
	try
		do shell script core & " uninstall"
	on error errMsg
		my failed(errMsg, myIcon)
	end try
	display dialog "Agent Presence was removed. You can drag this app to the Trash." with title appTitle buttons {"OK"} default button "OK" with icon myIcon
end doUninstall

on failed(errMsg, myIcon)
	set r to display dialog "Something went wrong:" & return & return & errMsg & return & return & ¬
		"The full log is in ~/Library/Logs/agent-presence-install.log" with title appTitle buttons {"Show Log", "Report Issue", "Quit"} default button "Quit" cancel button "Quit" with icon stop
	if button returned of r is "Show Log" then
		do shell script "open -R ~/Library/Logs/agent-presence-install.log"
	else if button returned of r is "Report Issue" then
		open location "https://github.com/abelxmendoza/discord-agent-presence/issues/new"
	end if
	error number -128
end failed
