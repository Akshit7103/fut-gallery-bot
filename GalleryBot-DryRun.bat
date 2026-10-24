@echo off
rem Same as GalleryBot.bat, but only checks live market prices - buys nothing.
call "%~dp0GalleryBot.bat" --dry-run
