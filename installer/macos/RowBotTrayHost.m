#import <Cocoa/Cocoa.h>
#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#import <signal.h>

static NSString * const RowBotBundleIdentifier = @"ai.row-bot.assistant";
static NSString * const RowBotAppName = @"Row-Bot";
static NSString * const RowBotStateFileName = @"launcher_state.json";
static NSString * const RowBotPingId = @"row-bot";
static NSInteger const RowBotDefaultPort = 8080;
static NSInteger const RowBotPortScanCount = 50;

static NSString *RowBotHomeDataDir(void) {
    NSString *envDataDir = [[[NSProcessInfo processInfo] environment] objectForKey:@"ROW_BOT_DATA_DIR"];
    if (envDataDir.length > 0) {
        return [envDataDir stringByExpandingTildeInPath];
    }
    return [NSHomeDirectory() stringByAppendingPathComponent:@".row-bot"];
}

static BOOL RowBotEnsureDirectory(NSString *path) {
    if (path.length == 0) {
        return NO;
    }
    NSError *error = nil;
    BOOL ok = [[NSFileManager defaultManager] createDirectoryAtPath:path
                                        withIntermediateDirectories:YES
                                                         attributes:nil
                                                              error:&error];
    return ok || error == nil;
}

static void RowBotLog(NSString *dataDir, NSString *format, ...) {
    if (dataDir.length == 0) {
        dataDir = RowBotHomeDataDir();
    }
    RowBotEnsureDirectory(dataDir);
    NSString *logPath = [dataDir stringByAppendingPathComponent:@"launcher-host.log"];

    va_list args;
    va_start(args, format);
    NSString *message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);

    NSDateFormatter *formatter = [[NSDateFormatter alloc] init];
    formatter.dateFormat = @"yyyy-MM-dd HH:mm:ss";
    NSString *line = [NSString stringWithFormat:@"%@ %@\n", [formatter stringFromDate:[NSDate date]], message];

    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:logPath];
    if (!handle) {
        [line writeToFile:logPath atomically:YES encoding:NSUTF8StringEncoding error:nil];
        return;
    }
    @try {
        [handle seekToEndOfFile];
        [handle writeData:[line dataUsingEncoding:NSUTF8StringEncoding]];
    } @catch (__unused NSException *exception) {
    } @finally {
        [handle closeFile];
    }
}

static NSDictionary *RowBotReadJSONFile(NSString *path) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (!data) {
        return nil;
    }
    NSError *error = nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![json isKindOfClass:[NSDictionary class]]) {
        return nil;
    }
    return (NSDictionary *)json;
}

static NSInteger RowBotIntegerValue(id value) {
    if ([value respondsToSelector:@selector(integerValue)]) {
        return [value integerValue];
    }
    return 0;
}

static NSString *RowBotHTTP(NSString *urlString, NSString *method, NSTimeInterval timeout) {
    NSURL *url = [NSURL URLWithString:urlString];
    if (!url) {
        return nil;
    }

    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = method ?: @"GET";
    request.timeoutInterval = timeout;
    if ([request.HTTPMethod isEqualToString:@"POST"]) {
        request.HTTPBody = [NSData data];
    }

    dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
    __block NSData *responseData = nil;
    __block NSHTTPURLResponse *httpResponse = nil;

    NSURLSessionConfiguration *config = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    config.timeoutIntervalForRequest = timeout;
    config.timeoutIntervalForResource = timeout;
    NSURLSession *session = [NSURLSession sessionWithConfiguration:config];
    NSURLSessionDataTask *task = [session dataTaskWithRequest:request
                                            completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        if (!error && [response isKindOfClass:[NSHTTPURLResponse class]]) {
            httpResponse = (NSHTTPURLResponse *)response;
            responseData = data;
        }
        dispatch_semaphore_signal(semaphore);
    }];
    [task resume];
    dispatch_time_t deadline = dispatch_time(DISPATCH_TIME_NOW, (int64_t)(timeout * NSEC_PER_SEC));
    if (dispatch_semaphore_wait(semaphore, deadline) != 0) {
        [task cancel];
        [session invalidateAndCancel];
        return nil;
    }
    [session finishTasksAndInvalidate];

    if (!httpResponse || httpResponse.statusCode < 200 || httpResponse.statusCode >= 300) {
        return nil;
    }
    if (!responseData) {
        return @"";
    }
    return [[NSString alloc] initWithData:responseData encoding:NSUTF8StringEncoding];
}

static BOOL RowBotPingPort(NSInteger port) {
    if (port <= 0) {
        return NO;
    }
    NSString *url = [NSString stringWithFormat:@"http://127.0.0.1:%ld/api/launcher-ping", (long)port];
    NSString *body = RowBotHTTP(url, @"GET", 0.6);
    if (body.length == 0) {
        return NO;
    }
    NSString *compact = [[[body lowercaseString] stringByReplacingOccurrencesOfString:@" " withString:@""]
                         stringByReplacingOccurrencesOfString:@"\n" withString:@""];
    return [compact containsString:[NSString stringWithFormat:@"\"app\":\"%@\"", RowBotPingId]];
}

@interface RowBotHost : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSTask *pythonTask;
@property(nonatomic, copy) NSString *resourceDir;
@property(nonatomic, copy) NSString *pythonPath;
@property(nonatomic, copy) NSString *appDir;
@property(nonatomic, copy) NSString *launcherPath;
@property(nonatomic, copy) NSString *dataDir;
@property(nonatomic, copy) NSString *statePath;
@property(nonatomic, assign) NSInteger lastWindowPid;
@end

@implementation RowBotHost

- (instancetype)init {
    self = [super init];
    if (!self) {
        return nil;
    }

    NSBundle *bundle = [NSBundle mainBundle];
    self.resourceDir = bundle.resourcePath ?: @"";
    self.pythonPath = [self.resourceDir stringByAppendingPathComponent:@"python/bin/python3"];
    self.appDir = [self.resourceDir stringByAppendingPathComponent:@"app"];
    self.launcherPath = [self.appDir stringByAppendingPathComponent:@"launcher.py"];
    self.dataDir = RowBotHomeDataDir();
    self.statePath = [self.dataDir stringByAppendingPathComponent:RowBotStateFileName];
    self.lastWindowPid = 0;
    RowBotEnsureDirectory(self.dataDir);
    return self;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [self installStatusItem];
    RowBotLog(self.dataDir, @"host_started bundle=%@ resources=%@ python=%@",
              [[NSBundle mainBundle] bundlePath], self.resourceDir, self.pythonPath);
    [self startPrimaryLauncherIfNeeded];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    (void)sender;
    return NO;
}

- (void)installStatusItem {
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.title = @"RB";
    self.statusItem.button.toolTip = RowBotAppName;
    self.statusItem.button.imagePosition = NSImageLeft;

    NSMenu *menu = [[NSMenu alloc] initWithTitle:RowBotAppName];
    NSMenuItem *openItem = [[NSMenuItem alloc] initWithTitle:@"Open Row-Bot"
                                                      action:@selector(openRowBot:)
                                               keyEquivalent:@""];
    openItem.target = self;
    [menu addItem:openItem];

    NSMenuItem *browserItem = [[NSMenuItem alloc] initWithTitle:@"Open in Browser"
                                                         action:@selector(openInBrowser:)
                                                  keyEquivalent:@""];
    browserItem.target = self;
    [menu addItem:browserItem];

    [menu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *showBuddyItem = [[NSMenuItem alloc] initWithTitle:@"Show Buddy"
                                                           action:@selector(showBuddy:)
                                                    keyEquivalent:@""];
    showBuddyItem.target = self;
    [menu addItem:showBuddyItem];

    NSMenuItem *hideBuddyItem = [[NSMenuItem alloc] initWithTitle:@"Hide Buddy"
                                                           action:@selector(hideBuddy:)
                                                    keyEquivalent:@""];
    hideBuddyItem.target = self;
    [menu addItem:hideBuddyItem];

    [menu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *quitItem = [[NSMenuItem alloc] initWithTitle:@"Quit"
                                                      action:@selector(quitRowBot:)
                                               keyEquivalent:@"q"];
    quitItem.target = self;
    [menu addItem:quitItem];

    self.statusItem.menu = menu;
}

- (NSDictionary *)currentState {
    NSDictionary *state = RowBotReadJSONFile(self.statePath);
    if (![state isKindOfClass:[NSDictionary class]]) {
        return nil;
    }
    NSString *app = [state objectForKey:@"app"];
    if (app.length > 0 && ![[app lowercaseString] isEqualToString:RowBotPingId]) {
        return nil;
    }
    return state;
}

- (NSInteger)activePort {
    NSDictionary *state = [self currentState];
    NSInteger statePort = RowBotIntegerValue([state objectForKey:@"port"]);
    if (statePort > 0 && RowBotPingPort(statePort)) {
        return statePort;
    }
    for (NSInteger port = RowBotDefaultPort; port < RowBotDefaultPort + RowBotPortScanCount; port++) {
        if (RowBotPingPort(port)) {
            return port;
        }
    }
    return 0;
}

- (NSInteger)activeControlPort {
    NSDictionary *state = [self currentState];
    NSInteger port = RowBotIntegerValue([state objectForKey:@"port"]);
    if (port <= 0 || !RowBotPingPort(port)) {
        return 0;
    }
    return RowBotIntegerValue([state objectForKey:@"window_control_port"]);
}

- (NSInteger)activeWindowPid {
    NSDictionary *state = [self currentState];
    NSInteger pid = RowBotIntegerValue([state objectForKey:@"window_pid"]);
    if (pid > 0) {
        self.lastWindowPid = pid;
    }
    return self.lastWindowPid;
}

- (NSMutableDictionary *)launcherEnvironment {
    NSMutableDictionary *env = [[[NSProcessInfo processInfo] environment] mutableCopy];
    [env setObject:self.resourceDir forKey:@"ROW_BOT_INSTALL_ROOT"];
    [env setObject:@"1" forKey:@"PYTHONNOUSERSITE"];
    [env setObject:@"utf-8" forKey:@"PYTHONIOENCODING"];
    [env setObject:@"1" forKey:@"PYTHONDONTWRITEBYTECODE"];
    [env setObject:@"1" forKey:@"ROW_BOT_NATIVE_TRAY_HOST"];

    NSString *bundledBrowsers = [self.resourceDir stringByAppendingPathComponent:@"python/playwright-browsers"];
    BOOL isDir = NO;
    if ([[NSFileManager defaultManager] fileExistsAtPath:bundledBrowsers isDirectory:&isDir] && isDir) {
        [env setObject:bundledBrowsers forKey:@"PLAYWRIGHT_BROWSERS_PATH"];
    } else {
        NSString *userBrowsers = [self.dataDir stringByAppendingPathComponent:@"playwright-browsers"];
        RowBotEnsureDirectory(userBrowsers);
        [env setObject:userBrowsers forKey:@"PLAYWRIGHT_BROWSERS_PATH"];
    }
    return env;
}

- (NSTask *)startPythonLauncherWithArguments:(NSArray<NSString *> *)arguments retainAsPrimary:(BOOL)retainAsPrimary {
    if (![[NSFileManager defaultManager] isExecutableFileAtPath:self.pythonPath]) {
        RowBotLog(self.dataDir, @"missing_bundled_python path=%@", self.pythonPath);
        return nil;
    }
    if (![[NSFileManager defaultManager] fileExistsAtPath:self.launcherPath]) {
        RowBotLog(self.dataDir, @"missing_launcher path=%@", self.launcherPath);
        return nil;
    }

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:self.pythonPath];
    task.currentDirectoryURL = [NSURL fileURLWithPath:self.appDir];
    task.arguments = arguments;
    task.environment = [self launcherEnvironment];

    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        RowBotLog(self.dataDir, @"python_launch_failed error=%@", error.localizedDescription ?: @"unknown");
        return nil;
    }
    RowBotLog(self.dataDir, @"python_launched pid=%d args=%@", task.processIdentifier, [arguments componentsJoinedByString:@" "]);

    if (retainAsPrimary) {
        self.pythonTask = task;
    }
    return task;
}

- (void)startPrimaryLauncherIfNeeded {
    NSInteger port = [self activePort];
    if (port > 0) {
        RowBotLog(self.dataDir, @"server_already_active port=%ld", (long)port);
        return;
    }
    if (self.pythonTask && self.pythonTask.running) {
        RowBotLog(self.dataDir, @"primary_launcher_already_running pid=%d", self.pythonTask.processIdentifier);
        return;
    }
    [self startPythonLauncherWithArguments:@[@"launcher.py", @"--no-tray", @"--native"]
                           retainAsPrimary:YES];
}

- (void)openRowBot:(id)sender {
    (void)sender;
    NSInteger port = [self activePort];
    if (port > 0) {
        [self startPythonLauncherWithArguments:@[
            @"launcher.py",
            @"--no-tray",
            @"--native",
            @"--no-splash",
            @"--no-ollama",
            @"--port",
            [NSString stringWithFormat:@"%ld", (long)port]
        ] retainAsPrimary:NO];
        return;
    }
    [self startPrimaryLauncherIfNeeded];
}

- (void)openInBrowser:(id)sender {
    (void)sender;
    NSInteger port = [self activePort];
    if (port <= 0) {
        [self startPrimaryLauncherIfNeeded];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            NSInteger retryPort = [self activePort];
            if (retryPort > 0) {
                NSString *retryURL = [NSString stringWithFormat:@"http://127.0.0.1:%ld", (long)retryPort];
                [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:retryURL]];
            } else {
                RowBotLog(self.dataDir, @"open_browser_retry_no_server");
            }
        });
        return;
    }
    NSString *urlString = [NSString stringWithFormat:@"http://127.0.0.1:%ld", (long)port];
    [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:urlString]];
}

- (BOOL)sendBuddyCommand:(NSString *)command {
    NSInteger controlPort = [self activeControlPort];
    if (controlPort <= 0) {
        return NO;
    }
    NSString *url = [NSString stringWithFormat:@"http://127.0.0.1:%ld/buddy/%@",
                     (long)controlPort, command ?: @""];
    NSString *body = RowBotHTTP(url, @"GET", 1.5);
    return body.length > 0 && [[body lowercaseString] containsString:@"true"];
}

- (void)showBuddy:(id)sender {
    (void)sender;
    if ([self sendBuddyCommand:@"show"]) {
        return;
    }
    [self openRowBot:nil];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self sendBuddyCommand:@"show"];
    });
}

- (void)hideBuddy:(id)sender {
    (void)sender;
    [self sendBuddyCommand:@"hide"];
}

- (void)terminateWindowIfKnown {
    NSInteger windowPid = [self activeWindowPid];
    if (windowPid > 0) {
        kill((pid_t)windowPid, SIGTERM);
        RowBotLog(self.dataDir, @"window_terminate_requested pid=%ld", (long)windowPid);
    }
}

- (void)quitRowBot:(id)sender {
    (void)sender;
    RowBotLog(self.dataDir, @"quit_requested");
    NSInteger port = [self activePort];
    if (port > 0) {
        NSString *url = [NSString stringWithFormat:@"http://127.0.0.1:%ld/api/launcher-shutdown", (long)port];
        RowBotHTTP(url, @"POST", 2.5);
    }
    [self terminateWindowIfKnown];
    [[NSFileManager defaultManager] removeItemAtPath:self.statePath error:nil];
    if (self.pythonTask && self.pythonTask.running) {
        [self.pythonTask terminate];
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (self.pythonTask && self.pythonTask.running) {
            [self.pythonTask terminate];
        }
        [[NSStatusBar systemStatusBar] removeStatusItem:self.statusItem];
        [NSApp terminate:nil];
    });
}

@end

static int RowBotSelfTest(void) {
    RowBotHost *host = [[RowBotHost alloc] init];
    NSFileManager *fm = [NSFileManager defaultManager];
    BOOL ok = YES;
    NSMutableArray<NSString *> *failures = [NSMutableArray array];

    if (![[[NSBundle mainBundle] bundleIdentifier] isEqualToString:RowBotBundleIdentifier]) {
        ok = NO;
        [failures addObject:@"bundle identifier mismatch"];
    }
    if (![fm isExecutableFileAtPath:host.pythonPath]) {
        ok = NO;
        [failures addObject:@"bundled python missing"];
    }
    if (![fm fileExistsAtPath:host.launcherPath]) {
        ok = NO;
        [failures addObject:@"launcher.py missing"];
    }
    if (host.resourceDir.length == 0 || host.appDir.length == 0) {
        ok = NO;
        [failures addObject:@"bundle resource paths missing"];
    }

    if (ok) {
        printf("row-bot-host-self-test ok bundle=%s python=%s app=%s\n",
               [[[NSBundle mainBundle] bundlePath] UTF8String],
               [host.pythonPath UTF8String],
               [host.appDir UTF8String]);
        return 0;
    }

    fprintf(stderr, "row-bot-host-self-test failed: %s\n", [[failures componentsJoinedByString:@"; "] UTF8String]);
    return 1;
}

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        for (int index = 1; index < argc; index++) {
            if (strcmp(argv[index], "--self-test") == 0) {
                return RowBotSelfTest();
            }
        }

        NSApplication *app = [NSApplication sharedApplication];
        RowBotHost *delegate = [[RowBotHost alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
