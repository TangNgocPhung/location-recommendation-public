@echo off
REM Vo boc cho nearby.ps1.
REM
REM Windows mac dinh dat ExecutionPolicy = Restricted cho nguoi dung, nen go
REM thang ".\nearby.ps1" se bi chan voi loi "running scripts is disabled on
REM this system". File .cmd khong bi chinh sach do rang buoc, va no goi
REM PowerShell voi -ExecutionPolicy Bypass chi cho DUY NHAT tien trinh nay,
REM khong sua chinh sach cua may.
REM
REM Chi dung ky tu ASCII va KHONG dung ky tu "duong ong" trong cac dong REM:
REM cmd.exe phan tich chung truoc khi nhan ra day la dong ghi chu, nen mot
REM ghi chu vo hai van sinh ra loi "is not recognized as an internal command".
REM
REM Cach dung:
REM   nearby up         bat toan bo stack
REM   nearby status     xem du lieu va mo hinh LTR
REM   nearby labels     sinh khung 400 cap can gan nhan
REM   nearby ratings    lay rating that tu Google Places (can API key)
REM   nearby train      Phase 4 + 5
REM   nearby eval       Phase 7
REM   nearby ablation   Phase 8
REM   nearby test       chay unit test
REM   nearby down       tat stack
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0nearby.ps1" %*
