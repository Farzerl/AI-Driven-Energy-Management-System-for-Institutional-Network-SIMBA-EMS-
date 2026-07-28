using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace SimbaEMS.WindowsLauncher
{
    internal static class Program
    {
        private const string MutexName = @"Local\SIMBA_EMS_LAUNCHER_SINGLE_INSTANCE";
        private const string StopEventName = @"Local\SIMBA_EMS_LAUNCHER_STOP";
        private static Mutex _instanceMutex;

        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            if (HasArgument(args, "--stop"))
            {
                SendStopRequest();
                return;
            }

            bool createdNew;
            _instanceMutex = new Mutex(true, MutexName, out createdNew);
            if (!createdNew)
            {
                OpenDashboardDirectly();
                return;
            }

            EventWaitHandle stopEvent;
            try
            {
                stopEvent = new EventWaitHandle(false, EventResetMode.AutoReset, StopEventName);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "SIMBA-EMS could not create its local stop control.\r\n\r\n" + ex.Message,
                    "SIMBA-EMS Launcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return;
            }

            try
            {
                Application.Run(new LauncherContext(stopEvent));
            }
            finally
            {
                stopEvent.Dispose();
                if (_instanceMutex != null)
                {
                    _instanceMutex.ReleaseMutex();
                    _instanceMutex.Dispose();
                }
            }
        }

        private static bool HasArgument(string[] args, string value)
        {
            if (args == null) return false;
            foreach (string arg in args)
            {
                if (string.Equals(arg, value, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }

        private static void SendStopRequest()
        {
            try
            {
                using (EventWaitHandle handle = EventWaitHandle.OpenExisting(StopEventName))
                {
                    handle.Set();
                }
                MessageBox.Show(
                    "A safe stop request was sent to SIMBA-EMS.",
                    "SIMBA-EMS Launcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }
            catch (WaitHandleCannotBeOpenedException)
            {
                MessageBox.Show(
                    "The graphical SIMBA-EMS launcher is not currently running.",
                    "SIMBA-EMS Launcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "SIMBA-EMS could not send the stop request.\r\n\r\n" + ex.Message,
                    "SIMBA-EMS Launcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }

        private static void OpenDashboardDirectly()
        {
            try
            {
                Process.Start(new ProcessStartInfo("http://127.0.0.1:8000/?tab=demo")
                {
                    UseShellExecute = true
                });
            }
            catch
            {
                MessageBox.Show(
                    "SIMBA-EMS is already starting or running. Open http://127.0.0.1:8000 in your browser.",
                    "SIMBA-EMS Launcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }
        }
    }

    internal sealed class LauncherContext : ApplicationContext
    {
        private const string DashboardUrl = "http://127.0.0.1:8000/?tab=demo";
        private const string HealthUrl = "http://127.0.0.1:8000/api/health";
        private const string ForecastUrl = "http://127.0.0.1:8000/api/live-forecasts?limit=1";

        private readonly EventWaitHandle _stopEvent;
        private readonly System.Windows.Forms.Timer _stopTimer;
        private readonly NotifyIcon _notifyIcon;
        private readonly SplashForm _splash;
        private readonly Control _dispatcher;
        private readonly object _logLock = new object();
        private readonly string _projectRoot;
        private readonly string _logDirectory;
        private readonly string _launcherLogPath;
        private readonly string _backendLogPath;
        private readonly string _statePath;

        private Process _backendProcess;
        private JobObject _backendJob;
        private bool _ownsBackend;
        private bool _stopping;

        public LauncherContext(EventWaitHandle stopEvent)
        {
            _stopEvent = stopEvent;
            _projectRoot = FindProjectRoot();
            _logDirectory = Path.Combine(_projectRoot, "runtime", "logs");
            _launcherLogPath = Path.Combine(_logDirectory, "simba_launcher.log");
            _backendLogPath = Path.Combine(_logDirectory, "simba_backend.log");
            _statePath = Path.Combine(_projectRoot, "runtime", "simba_launcher_state.txt");

            Directory.CreateDirectory(_logDirectory);
            Log("Launcher process started.");

            _dispatcher = new Control();
            _dispatcher.CreateControl();

            _splash = new SplashForm(LoadEmbeddedLogo());
            _splash.FormClosed += delegate { };
            _splash.Show();

            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("Open dashboard", null, delegate { OpenDashboard(); });
            menu.Items.Add("Open runtime logs", null, delegate { OpenLogs(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Stop SIMBA-EMS", null, delegate { BeginStop("Operator requested shutdown from the tray icon."); });

            Icon appIcon = null;
            try { appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            if (appIcon == null) appIcon = SystemIcons.Application;

            _notifyIcon = new NotifyIcon();
            _notifyIcon.Icon = appIcon;
            _notifyIcon.Text = "SIMBA-EMS launcher";
            _notifyIcon.ContextMenuStrip = menu;
            _notifyIcon.DoubleClick += delegate { OpenDashboard(); };
            _notifyIcon.Visible = true;

            _stopTimer = new System.Windows.Forms.Timer();
            _stopTimer.Interval = 500;
            _stopTimer.Tick += delegate
            {
                if (_stopEvent.WaitOne(0)) BeginStop("External safe stop request received.");
            };
            _stopTimer.Start();

            Thread worker = new Thread(StartSequence);
            worker.IsBackground = true;
            worker.Name = "SIMBA-EMS startup worker";
            worker.Start();
        }

        private void StartSequence()
        {
            try
            {
                SetSplash("Checking Windows 11 compatibility...", 8);
                ValidateWindows11();
                ThrowIfStopping();

                SetSplash("Checking the local SIMBA-EMS runtime...", 18);
                ValidateProjectFiles();
                ThrowIfStopping();

                bool alreadyHealthy = IsHealthReady();
                if (!alreadyHealthy)
                {
                    if (IsPortOpen("127.0.0.1", 8000))
                    {
                        throw new InvalidOperationException(
                            "Port 8000 is already in use by another program. Close that program and start SIMBA-EMS again.");
                    }

                    SetSplash("Resetting the demonstration session...", 28);
                    RunHiddenTool(
                        Path.Combine(_projectRoot, ".venv", "Scripts", "python.exe"),
                        "scripts\\reset_demo_runtime.py",
                        120000,
                        true);
                    ThrowIfStopping();

                    SetSplash("Starting the secure local service...", 42);
                    StartBackend();
                    ThrowIfStopping();

                    SetSplash("Running startup health checks...", 58);
                    WaitForHealth(90000);
                    ThrowIfStopping();
                }
                else
                {
                    Log("A healthy SIMBA-EMS service was already available on 127.0.0.1:8000.");
                }

                SetSplash("Loading authorised meter data...", 70);
                TryRunCollector();
                ThrowIfStopping();

                SetSplash("Preparing the latest forecasts...", 84);
                WaitForForecast(90000);
                ThrowIfStopping();

                SetSplash("Opening the SIMBA-EMS dashboard...", 96);
                OpenDashboard();

                WriteStateFile();
                Log("SIMBA-EMS startup completed successfully.");
                SetSplash("SIMBA-EMS is ready", 100);
                Thread.Sleep(900);
                CloseSplash();
            }
            catch (Exception ex)
            {
                if (_stopping)
                {
                    Log("Startup sequence stopped: " + ex.Message);
                    return;
                }
                Log("STARTUP ERROR: " + ex);
                ShowStartupError(ex.Message);
                BeginStop("Startup failed.");
            }
        }

        private void ValidateWindows11()
        {
            if (!Environment.Is64BitOperatingSystem || !Environment.Is64BitProcess)
            {
                throw new PlatformNotSupportedException(
                    "SIMBA-EMS requires 64-bit Windows 11 and a 64-bit launcher build.");
            }

            NativeMethods.RTL_OSVERSIONINFOEX version = new NativeMethods.RTL_OSVERSIONINFOEX();
            version.dwOSVersionInfoSize = (uint)Marshal.SizeOf(typeof(NativeMethods.RTL_OSVERSIONINFOEX));
            int result = NativeMethods.RtlGetVersion(ref version);
            if (result != 0 || version.dwMajorVersion < 10 || version.dwBuildNumber < 22000)
            {
                throw new PlatformNotSupportedException(
                    "This launcher is intended for Windows 11 x64 (build 22000 or later). Detected build: " +
                    version.dwBuildNumber + ".");
            }

            Log("Windows compatibility check passed: build " + version.dwBuildNumber + ", x64.");
        }

        private void ValidateProjectFiles()
        {
            string[] required = new string[]
            {
                Path.Combine(_projectRoot, ".venv", "Scripts", "python.exe"),
                Path.Combine(_projectRoot, "src", "api", "server.py"),
                Path.Combine(_projectRoot, "dashboard", "index.html"),
                Path.Combine(_projectRoot, "config", "edge.example.json")
            };

            foreach (string path in required)
            {
                if (!File.Exists(path))
                {
                    throw new FileNotFoundException(
                        "A required SIMBA-EMS file is missing: " + path +
                        "\r\n\r\nRun SETUP_AND_START_SIMBA_EMS.bat once, then rebuild or restart the launcher.");
                }
            }

            Log("Project file and virtual-environment checks passed.");
        }

        private void StartBackend()
        {
            string python = Path.Combine(_projectRoot, ".venv", "Scripts", "python.exe");
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = python;
            info.Arguments = "-m uvicorn src.api.server:create_app --factory --host 127.0.0.1 --port 8000";
            info.WorkingDirectory = _projectRoot;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;

            _backendProcess = new Process();
            _backendProcess.StartInfo = info;
            _backendProcess.EnableRaisingEvents = true;
            _backendProcess.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (!string.IsNullOrEmpty(e.Data)) AppendBackendLog("OUT", e.Data);
            };
            _backendProcess.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (!string.IsNullOrEmpty(e.Data)) AppendBackendLog("ERR", e.Data);
            };
            _backendProcess.Exited += delegate
            {
                if (!_stopping) Log("Backend process exited unexpectedly with code " + SafeExitCode(_backendProcess) + ".");
            };

            if (!_backendProcess.Start())
            {
                throw new InvalidOperationException("The SIMBA-EMS backend process could not be started.");
            }

            _backendProcess.BeginOutputReadLine();
            _backendProcess.BeginErrorReadLine();
            _ownsBackend = true;

            try
            {
                _backendJob = new JobObject();
                _backendJob.AddProcess(_backendProcess);
            }
            catch (Exception ex)
            {
                if (_backendJob != null) _backendJob.Dispose();
                _backendJob = null;
                Log("Job-object warning; process-level shutdown fallback will be used: " + ex.Message);
            }

            Log("Backend started without a console window. PID=" + _backendProcess.Id + ".");
        }

        private void WaitForHealth(int timeoutMilliseconds)
        {
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
            while (DateTime.UtcNow < deadline)
            {
                ThrowIfStopping();
                if (_backendProcess != null && _backendProcess.HasExited)
                {
                    throw new InvalidOperationException(
                        "The local service stopped during startup. Review " + _backendLogPath + ".");
                }

                if (IsHealthReady()) return;
                Thread.Sleep(1000);
            }

            throw new TimeoutException(
                "SIMBA-EMS did not pass its startup health check within 90 seconds. Review " + _backendLogPath + ".");
        }

        private bool IsHealthReady()
        {
            string body;
            if (!TryGet(HealthUrl, 2500, out body)) return false;
            string compact = body.Replace(" ", string.Empty).Replace("\r", string.Empty).Replace("\n", string.Empty);
            return compact.IndexOf("\"status\":\"online\"", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private void TryRunCollector()
        {
            try
            {
                string python = Path.Combine(_projectRoot, ".venv", "Scripts", "python.exe");
                RunHiddenTool(
                    python,
                    "-m src.edge.collector --config config\\edge.example.json --once",
                    180000,
                    true);
            }
            catch (Exception ex)
            {
                Log("Collector warning: " + ex.Message);
            }
        }

        private void WaitForForecast(int timeoutMilliseconds)
        {
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
            while (DateTime.UtcNow < deadline)
            {
                ThrowIfStopping();
                string body;
                if (TryGet(ForecastUrl, 3000, out body))
                {
                    string compact = body.Replace(" ", string.Empty).Replace("\r", string.Empty).Replace("\n", string.Empty);
                    if (compact.IndexOf("\"items\":[{", StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        Log("Live forecast readiness check passed.");
                        return;
                    }
                }
                Thread.Sleep(1000);
            }

            Log("Forecast readiness timed out; the healthy dashboard will still be opened.");
        }

        private void RunHiddenTool(string fileName, string arguments, int timeoutMilliseconds, bool requireZeroExit)
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = fileName;
            info.Arguments = arguments;
            info.WorkingDirectory = _projectRoot;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;

            using (Process process = new Process())
            {
                process.StartInfo = info;
                StringBuilder output = new StringBuilder();
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (!string.IsNullOrEmpty(e.Data)) output.AppendLine(e.Data);
                };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (!string.IsNullOrEmpty(e.Data)) output.AppendLine(e.Data);
                };

                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();

                if (!process.WaitForExit(timeoutMilliseconds))
                {
                    try { process.Kill(); } catch { }
                    throw new TimeoutException("A SIMBA-EMS startup task exceeded its permitted runtime.");
                }

                process.WaitForExit();
                if (output.Length > 0) Log(output.ToString().Trim());
                if (requireZeroExit && process.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        "A SIMBA-EMS startup task returned exit code " + process.ExitCode + ".");
                }
            }
        }

        private static bool TryGet(string url, int timeoutMilliseconds, out string body)
        {
            body = string.Empty;
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
                request.Method = "GET";
                request.Timeout = timeoutMilliseconds;
                request.ReadWriteTimeout = timeoutMilliseconds;
                request.Proxy = null;
                request.UserAgent = "SIMBA-EMS-Windows-Launcher/1.0";

                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (Stream stream = response.GetResponseStream())
                using (StreamReader reader = new StreamReader(stream))
                {
                    body = reader.ReadToEnd();
                    return response.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        private static bool IsPortOpen(string host, int port)
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult result = client.BeginConnect(host, port, null, null);
                    bool connected = result.AsyncWaitHandle.WaitOne(800);
                    if (!connected) return false;
                    client.EndConnect(result);
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }

        private void OpenDashboard()
        {
            try
            {
                Process.Start(new ProcessStartInfo(DashboardUrl) { UseShellExecute = true });
                Log("Dashboard opened in the default browser.");
            }
            catch (Exception ex)
            {
                Log("Browser open error: " + ex.Message);
                ShowMessage(
                    "Open " + DashboardUrl + " in your browser.",
                    "SIMBA-EMS Dashboard",
                    MessageBoxIcon.Information);
            }
        }

        private void OpenLogs()
        {
            try
            {
                Process.Start(new ProcessStartInfo("explorer.exe", "\"" + _logDirectory + "\"")
                {
                    UseShellExecute = true
                });
            }
            catch (Exception ex)
            {
                ShowMessage(_logDirectory + "\r\n\r\n" + ex.Message, "SIMBA-EMS Logs", MessageBoxIcon.Information);
            }
        }

        private void BeginStop(string reason)
        {
            if (_stopping) return;
            _stopping = true;
            Log(reason);

            if (_splash != null && !_splash.IsDisposed)
            {
                try { _splash.BeginInvoke(new Action(delegate { _splash.SetStatus("Stopping SIMBA-EMS safely...", 100); })); } catch { }
            }

            Thread worker = new Thread(new ThreadStart(delegate
            {
                StopOwnedBackend();
                try { if (File.Exists(_statePath)) File.Delete(_statePath); } catch { }

                try
                {
                    _dispatcher.BeginInvoke(new Action(delegate
                    {
                        try { _notifyIcon.Visible = false; _notifyIcon.Dispose(); } catch { }
                        try { _stopTimer.Stop(); _stopTimer.Dispose(); } catch { }
                        ExitThread();
                    }));
                }
                catch
                {
                    Application.Exit();
                }
            }));
            worker.IsBackground = true;
            worker.Start();
        }

        private void StopOwnedBackend()
        {
            if (!_ownsBackend)
            {
                Log("The detected backend was not started by this launcher; it was left running.");
                return;
            }

            try
            {
                if (_backendJob != null)
                {
                    _backendJob.Terminate(0);
                    _backendJob.Dispose();
                    _backendJob = null;
                    Log("The launcher-owned backend process tree was stopped.");
                }
                else if (_backendProcess != null && !_backendProcess.HasExited)
                {
                    _backendProcess.Kill();
                    _backendProcess.WaitForExit(5000);
                    Log("The launcher-owned backend process was stopped.");
                }
            }
            catch (Exception ex)
            {
                Log("Shutdown warning: " + ex.Message);
            }
            finally
            {
                if (_backendProcess != null) _backendProcess.Dispose();
                _backendProcess = null;
            }
        }

        private void ThrowIfStopping()
        {
            if (_stopping) throw new OperationCanceledException("SIMBA-EMS startup was cancelled.");
        }

        private void SetSplash(string text, int progress)
        {
            Log(text);
            if (_splash == null || _splash.IsDisposed) return;
            try
            {
                _splash.BeginInvoke(new Action(delegate { _splash.SetStatus(text, progress); }));
            }
            catch { }
        }

        private void CloseSplash()
        {
            if (_splash == null || _splash.IsDisposed) return;
            try { _splash.BeginInvoke(new Action(delegate { _splash.Close(); })); } catch { }
        }

        private void ShowStartupError(string message)
        {
            ShowMessage(
                message + "\r\n\r\nTechnical details were written to:\r\n" + _launcherLogPath,
                "SIMBA-EMS could not start",
                MessageBoxIcon.Error);
        }

        private static void ShowMessage(string message, string title, MessageBoxIcon icon)
        {
            MessageBox.Show(message, title, MessageBoxButtons.OK, icon);
        }

        private void WriteStateFile()
        {
            try
            {
                StringBuilder state = new StringBuilder();
                state.AppendLine("launcher_pid=" + Process.GetCurrentProcess().Id);
                state.AppendLine("backend_pid=" + ((_backendProcess != null && !_backendProcess.HasExited) ? _backendProcess.Id.ToString() : "external"));
                state.AppendLine("dashboard_url=" + DashboardUrl);
                state.AppendLine("owns_backend=" + _ownsBackend.ToString().ToLowerInvariant());
                state.AppendLine("started_utc=" + DateTime.UtcNow.ToString("o"));
                File.WriteAllText(_statePath, state.ToString(), Encoding.UTF8);
            }
            catch (Exception ex)
            {
                Log("State-file warning: " + ex.Message);
            }
        }

        private void AppendBackendLog(string stream, string message)
        {
            try
            {
                lock (_logLock)
                {
                    File.AppendAllText(
                        _backendLogPath,
                        DateTime.UtcNow.ToString("o") + " [" + stream + "] " + message + Environment.NewLine,
                        Encoding.UTF8);
                }
            }
            catch { }
        }

        private void Log(string message)
        {
            try
            {
                lock (_logLock)
                {
                    File.AppendAllText(
                        _launcherLogPath,
                        DateTime.UtcNow.ToString("o") + " " + message + Environment.NewLine,
                        Encoding.UTF8);
                }
            }
            catch { }
        }

        private static int SafeExitCode(Process process)
        {
            try { return process.ExitCode; } catch { return -1; }
        }

        private static string FindProjectRoot()
        {
            DirectoryInfo current = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            for (int i = 0; i < 6 && current != null; i++)
            {
                if (File.Exists(Path.Combine(current.FullName, "START_SIMBA_EMS.bat")) &&
                    Directory.Exists(Path.Combine(current.FullName, "src")) &&
                    Directory.Exists(Path.Combine(current.FullName, "dashboard")))
                {
                    return current.FullName;
                }
                current = current.Parent;
            }

            return AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        }

        private static Image LoadEmbeddedLogo()
        {
            try
            {
                using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("SimbaLogo"))
                {
                    if (stream == null) return null;
                    using (Image source = Image.FromStream(stream))
                    {
                        return new Bitmap(source);
                    }
                }
            }
            catch
            {
                return null;
            }
        }

        protected override void ExitThreadCore()
        {
            try { StopOwnedBackend(); } catch { }
            try { _notifyIcon.Visible = false; _notifyIcon.Dispose(); } catch { }
            try { _stopTimer.Stop(); _stopTimer.Dispose(); } catch { }
            try { if (_splash != null && !_splash.IsDisposed) _splash.Close(); } catch { }
            try { if (_dispatcher != null) _dispatcher.Dispose(); } catch { }
            base.ExitThreadCore();
        }
    }

    internal sealed class SplashForm : Form
    {
        private readonly Label _statusLabel;
        private readonly ProgressBar _progressBar;

        public SplashForm(Image logo)
        {
            Text = "SIMBA-EMS";
            ClientSize = new Size(560, 330);
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(247, 248, 250);
            ShowInTaskbar = false;
            TopMost = true;

            Panel accent = new Panel();
            accent.Dock = DockStyle.Left;
            accent.Width = 10;
            accent.BackColor = Color.FromArgb(18, 108, 187);
            Controls.Add(accent);

            PictureBox picture = new PictureBox();
            picture.Location = new Point(38, 42);
            picture.Size = new Size(112, 112);
            picture.SizeMode = PictureBoxSizeMode.Zoom;
            picture.Image = logo;
            Controls.Add(picture);

            Label title = new Label();
            title.AutoSize = true;
            title.Location = new Point(174, 54);
            title.Font = new Font("Segoe UI", 25F, FontStyle.Bold, GraphicsUnit.Point);
            title.ForeColor = Color.FromArgb(32, 34, 37);
            title.Text = "SIMBA-EMS";
            Controls.Add(title);

            Label subtitle = new Label();
            subtitle.AutoSize = true;
            subtitle.Location = new Point(178, 105);
            subtitle.Font = new Font("Segoe UI", 11.5F, FontStyle.Regular, GraphicsUnit.Point);
            subtitle.ForeColor = Color.FromArgb(82, 86, 92);
            subtitle.Text = "Intelligent institutional energy management";
            Controls.Add(subtitle);

            Label loading = new Label();
            loading.AutoSize = true;
            loading.Location = new Point(39, 198);
            loading.Font = new Font("Segoe UI", 10F, FontStyle.Bold, GraphicsUnit.Point);
            loading.ForeColor = Color.FromArgb(32, 34, 37);
            loading.Text = "STARTING LOCAL ENERGY INTELLIGENCE";
            Controls.Add(loading);

            _statusLabel = new Label();
            _statusLabel.Location = new Point(39, 226);
            _statusLabel.Size = new Size(480, 28);
            _statusLabel.Font = new Font("Segoe UI", 10.5F, FontStyle.Regular, GraphicsUnit.Point);
            _statusLabel.ForeColor = Color.FromArgb(82, 86, 92);
            _statusLabel.Text = "Preparing SIMBA-EMS...";
            Controls.Add(_statusLabel);

            _progressBar = new ProgressBar();
            _progressBar.Location = new Point(39, 272);
            _progressBar.Size = new Size(480, 12);
            _progressBar.Minimum = 0;
            _progressBar.Maximum = 100;
            _progressBar.Value = 2;
            _progressBar.Style = ProgressBarStyle.Continuous;
            Controls.Add(_progressBar);

            Label localOnly = new Label();
            localOnly.AutoSize = true;
            localOnly.Location = new Point(39, 297);
            localOnly.Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point);
            localOnly.ForeColor = Color.FromArgb(110, 114, 120);
            localOnly.Text = "Local service: 127.0.0.1  |  No administrator rights required";
            Controls.Add(localOnly);
        }

        public void SetStatus(string text, int progress)
        {
            _statusLabel.Text = text;
            _progressBar.Value = Math.Max(_progressBar.Minimum, Math.Min(_progressBar.Maximum, progress));
            Refresh();
        }
    }

    internal sealed class JobObject : IDisposable
    {
        private IntPtr _handle;

        public JobObject()
        {
            _handle = NativeMethods.CreateJobObject(IntPtr.Zero, null);
            if (_handle == IntPtr.Zero)
            {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }

            NativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new NativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            info.BasicLimitInformation.LimitFlags = NativeMethods.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            int length = Marshal.SizeOf(typeof(NativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr pointer = Marshal.AllocHGlobal(length);
            try
            {
                Marshal.StructureToPtr(info, pointer, false);
                if (!NativeMethods.SetInformationJobObject(
                    _handle,
                    NativeMethods.JobObjectInfoType.ExtendedLimitInformation,
                    pointer,
                    (uint)length))
                {
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                }
            }
            finally
            {
                Marshal.FreeHGlobal(pointer);
            }
        }

        public void AddProcess(Process process)
        {
            if (!NativeMethods.AssignProcessToJobObject(_handle, process.Handle))
            {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
        }

        public void Terminate(uint exitCode)
        {
            if (_handle == IntPtr.Zero) return;
            NativeMethods.TerminateJobObject(_handle, exitCode);
        }

        public void Dispose()
        {
            if (_handle != IntPtr.Zero)
            {
                NativeMethods.CloseHandle(_handle);
                _handle = IntPtr.Zero;
            }
        }
    }

    internal static class NativeMethods
    {
        internal const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;

        internal enum JobObjectInfoType
        {
            ExtendedLimitInformation = 9
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct IO_COUNTERS
        {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        internal struct RTL_OSVERSIONINFOEX
        {
            public uint dwOSVersionInfoSize;
            public uint dwMajorVersion;
            public uint dwMinorVersion;
            public uint dwBuildNumber;
            public uint dwPlatformId;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string szCSDVersion;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool SetInformationJobObject(
            IntPtr job,
            JobObjectInfoType infoType,
            IntPtr info,
            uint infoLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool TerminateJobObject(IntPtr job, uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool CloseHandle(IntPtr handle);

        [DllImport("ntdll.dll", CharSet = CharSet.Unicode)]
        internal static extern int RtlGetVersion(ref RTL_OSVERSIONINFOEX versionInfo);
    }
}
