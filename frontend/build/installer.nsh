# AetherChat keeps conversation history, credentials, and UI settings in the
# Electron user-data directory. Never let an installer or uninstaller command
# line opt into deleting that directory.
!macro AetherChatSafeGetOptions _PARAMETERS _OPTION _RESULT
  ${If} ${_OPTION} == "--delete-app-data"
    StrCpy ${_RESULT} ""
    SetErrors
  ${Else}
    !insertmacro GetOptionsCall ${_PARAMETERS} ${_OPTION} ${_RESULT}
  ${EndIf}
!macroend

!macro customHeader
  !undef GetOptions
  !define GetOptions `!insertmacro AetherChatSafeGetOptions`
!macroend
