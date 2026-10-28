@echo off
:: 关闭命令行本身的命令回显，只显示我们自己echo打印的内容

echo ============================================
echo  Evan_Gerber_View: Building executable...
echo ============================================
echo.
:: echo. 是空一行，用来换行

:: 执行 pip show pyinstaller，检查是否已经安装pyinstaller
:: >nul 2>&1 把标准输出、错误输出全部屏蔽，不在控制台打印
pip show pyinstaller >nul 2>&1
:: %errorlevel% 是上一条命令的返回码，不等于0代表命令执行失败（没找到pyinstaller）
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

:: 判断当前目录下是否不存在 Evan_Gerber_View.ico 图标文件
if not exist "Evan_Gerber_View.ico" (
    echo Generating icon...
    python make_icon.py  :: 运行python脚本，自动生成图标
)

echo Cleaning old build...
:: 如果存在旧的build文件夹，强制递归删除 /s 删除子目录 /q 静默删除，不询问确认
if exist "build\Evan_Gerber_View" rmdir /s /q "build\Evan_Gerber_View"
:: 删除旧的dist输出目录
if exist "dist\Evan_Gerber_View"  rmdir /s /q "dist\Evan_Gerber_View"

echo.
echo Building with PyInstaller...
:: 调用PyInstaller，根据spec配置文件打包
:: --clean：打包前清理临时缓存
:: --noconfirm：所有确认提示自动选yes，不弹窗等待
python -m PyInstaller --clean --noconfirm "Evan_Gerber_View.spec"

:: 判断打包命令是否成功，errorlevel=0代表成功
if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo  Build SUCCESS!
    echo  Output: dist\Evan_Gerber_View\Evan_Gerber_View.exe
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  Build FAILED!
    echo ============================================
)

pause  :: 暂停窗口，按任意键才关闭，防止黑框一闪而过
