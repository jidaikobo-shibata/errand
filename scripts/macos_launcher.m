/* Keep the macOS application identity while running Homebrew's Python. */
#import <AppKit/AppKit.h>
#include <dlfcn.h>
#include <stdlib.h>

static int fail(NSString *message)
{
    [NSApplication sharedApplication];
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Errandを開けません";
    alert.informativeText = message;
    alert.alertStyle = NSAlertStyleCritical;
    [alert runModal];
    return 1;
}

int main(int argc, char **argv)
{
    @autoreleasepool {
        NSString *resources = [NSBundle mainBundle].resourcePath;
        NSString *path = [@"/opt/homebrew/bin:/usr/local/bin:" stringByAppendingString:
                          NSProcessInfo.processInfo.environment[@"PATH"] ?: @"/usr/bin:/bin"];
        setenv("PATH", path.UTF8String, 1);
        NSTask *probe = [[NSTask alloc] init];
        NSPipe *output = [NSPipe pipe];
        probe.launchPath = @"/bin/bash";
#if defined(__arm64__)
        NSString *architecture = @"arm64";
#else
        NSString *architecture = @"x86_64";
#endif
        probe.arguments = @[[resources stringByAppendingPathComponent:@"find-python"], architecture];
        probe.standardOutput = output;
        [probe launch];
        NSData *data = [output.fileHandleForReading readDataToEndOfFile];
        [probe waitUntilExit];
        NSArray *runtime = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        if (probe.terminationStatus || ![runtime isKindOfClass:[NSArray class]] || runtime.count != 3 ||
            ![runtime[0] isKindOfClass:[NSString class]] || ![runtime[1] isKindOfClass:[NSString class]] ||
            ![runtime[2] isKindOfClass:[NSString class]])
            return fail(@"実行環境が見つかりません。HomebrewでGTK 4・libadwaita・PyGObjectを導入してから、もう一度開いてください。");
        void *python = dlopen([runtime[1] fileSystemRepresentation], RTLD_NOW | RTLD_GLOBAL);
        int (*python_main)(int, char **) = python ? dlsym(python, "Py_BytesMain") : NULL;
        if (!python_main)
            return fail(@"Pythonの実行環境を読み込めませんでした。HomebrewのPythonを確認してください。");
        char **arguments = calloc((size_t)argc + 3, sizeof(char *));
        if (!arguments)
            return fail(@"起動に必要なメモリを確保できませんでした。");
        arguments[0] = (char *)[runtime[0] fileSystemRepresentation];
        arguments[1] = (char *)[[resources stringByAppendingPathComponent:@"native_startup.py"] fileSystemRepresentation];
        arguments[2] = (char *)[runtime[2] fileSystemRepresentation];
        for (int i = 1; i < argc; i++)
            arguments[i + 2] = argv[i];
        int result = python_main(argc + 2, arguments);
        free(arguments);
        return result;
    }
}
