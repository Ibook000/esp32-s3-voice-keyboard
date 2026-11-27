#include <stdio.h>
#include <wchar.h>
#include <windows.h>

#define PATH_CAPACITY 32768

static int show_error(const wchar_t *message) {
    MessageBoxW(NULL, message, L"ibook Keyboard", MB_OK | MB_ICONERROR);
    return 1;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show) {
    wchar_t root[PATH_CAPACITY];
    wchar_t python[PATH_CAPACITY];
    wchar_t command[PATH_CAPACITY];
    DWORD location_length = GetModuleFileNameW(NULL, root, PATH_CAPACITY);
    if (location_length == 0 || location_length >= PATH_CAPACITY) {
        return show_error(L"无法找到工作台目录。");
    }

    wchar_t *separator = wcsrchr(root, L'\\');
    if (separator == NULL) {
        return show_error(L"无法找到工作台目录。");
    }
    *separator = L'\0';

    int python_length = swprintf(python, PATH_CAPACITY, L"%ls\\runtime\\pythonw.exe", root);
    if (python_length < 0 || python_length >= PATH_CAPACITY) {
        return show_error(L"工作台目录路径过长。");
    }
    int command_length = swprintf(
        command, PATH_CAPACITY, L"\"%ls\" -X utf8 \"%ls\\pc\\web_config.py\" %ls",
        python, root, *arguments == L'\0' ? L"--open" : arguments
    );
    if (command_length < 0 || command_length >= PATH_CAPACITY) {
        return show_error(L"工作台启动命令过长。");
    }

    STARTUPINFOW startup = {0};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {0};
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, 0, NULL, root, &startup, &process)) {
        wchar_t message[256];
        swprintf(message, 256, L"工作台启动失败（错误 %lu）。请确认已完整解压软件包。",
                 GetLastError());
        return show_error(message);
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
