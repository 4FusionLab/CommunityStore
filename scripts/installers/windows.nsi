Unicode true
RequestExecutionLevel user
Name "Community Store"
Icon "setup.ico"
OutFile "CommunityStore-Setup.exe"
ShowInstDetails show
SetCompressor /SOLID lzma

!include "MUI2.nsh"

Var DownloadStatus

!define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_WELCOMEPAGE_TITLE "Community Store"
!define MUI_WELCOMEPAGE_TEXT "$(WelcomeText)"
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_SHOW ShowProgress
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "$(ReadyTitle)"
!define MUI_FINISHPAGE_TEXT "$(FinishText)"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Russian"

LangString WelcomeText ${LANG_ENGLISH} "A catalog of add-ins, right inside Autodesk Fusion.$\r$\n$\r$\nIf Fusion is open, save your work and close it. Setup will download the latest stable version."
LangString WelcomeText ${LANG_RUSSIAN} "Каталог расширений прямо в Autodesk Fusion.$\r$\n$\r$\nЕсли Fusion открыт, сохраните работу и закройте его. Установщик скачает последнюю стабильную версию."
LangString ProgressIntro ${LANG_ENGLISH} "Getting Community Store ready for Fusion."
LangString ProgressIntro ${LANG_RUSSIAN} "Готовим Community Store к запуску в Fusion."
LangString ReadyTitle ${LANG_ENGLISH} "Ready in Fusion"
LangString ReadyTitle ${LANG_RUSSIAN} "Всё готово"
LangString FinishText ${LANG_ENGLISH} "Open Fusion and go to:$\r$\n$\r$\nDesign → Utilities → Add-Ins → Community Store$\r$\n$\r$\nWhen you close this window, Setup will move the downloaded installer to the Recycle Bin."
LangString FinishText ${LANG_RUSSIAN} "Откройте Fusion и выберите:$\r$\n$\r$\nDesign → Utilities → Add-Ins → Community Store$\r$\n$\r$\nПосле закрытия окна установщик отправится в Корзину."
LangString ProgressFlag ${LANG_ENGLISH} "--progress-en"
LangString ProgressFlag ${LANG_RUSSIAN} "--progress-ru"
LangString FoundExisting ${LANG_ENGLISH} "Community Store is already present in a Fusion add-ins folder. Open Fusion and check Utilities → Add-Ins. You can update Community Store from its settings."
LangString FoundExisting ${LANG_RUSSIAN} "Community Store уже есть в папке расширений Fusion. Откройте Fusion и проверьте Utilities → Add-Ins. Обновление доступно в настройках Community Store."
LangString InstallError ${LANG_ENGLISH} "Community Store could not be installed. Check that you can write to your Autodesk user folder and try again."
LangString InstallError ${LANG_RUSSIAN} "Не удалось установить Community Store. Проверьте, что папка Autodesk в вашем профиле доступна для записи, и повторите попытку."
LangString DownloadError ${LANG_ENGLISH} "The release could not be downloaded or verified. Check your connection and try again."
LangString DownloadError ${LANG_RUSSIAN} "Не удалось скачать или проверить релиз. Проверьте подключение и повторите попытку."
LangString ReleaseMissing ${LANG_ENGLISH} "A stable release is not available on GitHub yet. Try again when 4FusionLab publishes it."
LangString ReleaseMissing ${LANG_RUSSIAN} "Стабильный релиз пока недоступен на GitHub. Повторите после его публикации 4FusionLab."

Function ShowProgress
  ${NSD_SetText} $mui.InstFilesPage.Text "$(ProgressIntro)"
  System::Call 'user32::GetWindowLongW(p $mui.InstFilesPage.ProgressBar, i -16) i .r0'
  IntOp $0 $0 | 0x08
  System::Call 'user32::SetWindowLongW(p $mui.InstFilesPage.ProgressBar, i -16, i r0)'
  SendMessage $mui.InstFilesPage.ProgressBar 0x040A 1 25
FunctionEnd

Section
  SetDetailsPrint none
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "community-store-downloader.exe"
  SetDetailsPrint listonly
  nsExec::ExecToLog '"$PLUGINSDIR\community-store-downloader.exe" $(ProgressFlag)'
  Pop $DownloadStatus
  SendMessage $mui.InstFilesPage.ProgressBar 0x040A 0 0
  SetOutPath "$TEMP"
  StrCmp $DownloadStatus "0" done
  StrCmp $DownloadStatus "10" installed
  StrCmp $DownloadStatus "20" download_error
  StrCmp $DownloadStatus "21" release_missing
  MessageBox MB_OK|MB_ICONSTOP "$(InstallError)" /SD IDOK
  Goto failed
download_error:
  MessageBox MB_OK|MB_ICONSTOP "$(DownloadError)" /SD IDOK
  Goto failed
release_missing:
  MessageBox MB_OK|MB_ICONSTOP "$(ReleaseMissing)" /SD IDOK
  Goto failed
installed:
  MessageBox MB_OK|MB_ICONINFORMATION "$(FoundExisting)" /SD IDOK
  Quit
failed:
  SetErrorLevel 1
  Quit
done:
  SetDetailsPrint none
  File "/oname=$PLUGINSDIR\modern-wizard.bmp" "ready.bmp"
  System::Call 'kernel32::GetCurrentProcessId() i .r0'
  nsExec::Exec '"$PLUGINSDIR\community-store-downloader.exe" --recycle-setup "$0" "$EXEPATH"'
  Pop $0
SectionEnd
