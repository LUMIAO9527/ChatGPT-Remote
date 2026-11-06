// Stable public executable; the sealed Python core is shared by all app roles.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("ChatGPT Remote")]
[assembly: AssemblyProduct("ChatGPT Remote")]
[assembly: AssemblyVersion("__FILE_VERSION__")]
[assembly: AssemblyFileVersion("__FILE_VERSION__")]
[assembly: AssemblyInformationalVersion("__PAYLOAD_VERSION__")]

internal static class RemoteLauncher
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int type, IntPtr info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);
    [StructLayout(LayoutKind.Sequential)]
    private struct JobLimits
    {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinimumWorkingSet, MaximumWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public JobLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    private static IntPtr CreateCoreJob()
    {
        var job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        var limits = new ExtendedLimits();
        // Stop-ScheduledTask terminates its action, so the launcher owns the
        // complete core tree. Independent update/uninstall workers explicitly
        // break away before the application closes.
        limits.Basic.Flags = 0x2000 | 0x800; // KILL_ON_JOB_CLOSE | BREAKAWAY_OK
        var pointer = Marshal.AllocHGlobal(Marshal.SizeOf(limits));
        try
        {
            Marshal.StructureToPtr(limits, pointer, false);
            if (!SetInformationJobObject(job, 9, pointer, (uint)Marshal.SizeOf(limits)))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            return job;
        }
        catch { CloseHandle(job); throw; }
        finally { Marshal.FreeHGlobal(pointer); }
    }
    private const string PayloadSha = "__PAYLOAD_SHA256__";
    private const string ManifestSha = "__MANIFEST_SHA256__";
    private const string Version = "__PAYLOAD_VERSION__";
    private const string AppId = "lumiao.chatgpt.remote";
    private static string PayloadId { get { return Version + "-" + PayloadSha.Substring(0, 16); } }

    private static Dictionary<string, object> Installed(string root)
    {
        try
        {
            var record = UpdateFiles.Read(UpdateFiles.Child(root, ".chatgptremote-install.json"));
            if (UpdateFiles.Num(record, "schema") == 1 && UpdateFiles.Str(record, "app") == AppId
                    && UpdateFiles.Same(UpdateFiles.Str(record, "root"), root)
                    && Regex.IsMatch(UpdateFiles.Str(record, "id"), "^[a-f0-9]{32}$")) return record;
        }
        catch { }
        return null;
    }

    private static string Redirect(string root)
    {
        try
        {
            var record = UpdateFiles.Read(UpdateFiles.Child(root, ".chatgptremote-location"));
            if (UpdateFiles.Num(record, "schema") != 1 || UpdateFiles.Str(record, "app") != AppId) return null;
            var targetRoot = UpdateFiles.Full(UpdateFiles.Str(record, "root"));
            if (UpdateFiles.Same(targetRoot, root)) return null;
            var installed = Installed(targetRoot);
            var target = UpdateFiles.Child(targetRoot, "ChatGPTRemote.exe");
            if (installed != null && UpdateFiles.Str(installed, "id") == UpdateFiles.Str(record, "id") && File.Exists(target)) return target;
        }
        catch { }
        return null;
    }

    private static string PayloadPath(string directory, string name)
    {
        if (Path.IsPathRooted(name) || name.Contains(":")) throw new InvalidDataException("unsafe_payload_path");
        var path = UpdateFiles.Full(Path.Combine(directory, name.Replace('/', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(UpdateFiles.Full(directory) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("unsafe_payload_path");
        UpdateFiles.Plain(path);
        return path;
    }

    private static void Verify(string directory)
    {
        var manifest = UpdateFiles.Child(directory, "CORE-MANIFEST.json");
        if (UpdateFiles.Digest(manifest) != ManifestSha) throw new InvalidDataException("core_manifest_changed");
        var entries = (object[])UpdateFiles.Read(manifest)["files"];
        foreach (Dictionary<string, object> entry in entries)
        {
            var path = PayloadPath(directory, UpdateFiles.Str(entry, "path"));
            if (!File.Exists(UpdateFiles.Extended(path)) || new FileInfo(UpdateFiles.Extended(path)).Length != UpdateFiles.Num(entry, "size")
                    || UpdateFiles.Digest(path) != UpdateFiles.Str(entry, "sha256"))
                throw new InvalidDataException("core_checksum_failed");
        }
    }

    private static string Materialize(string caches)
    {
        var directory = UpdateFiles.Child(caches, PayloadId);
        using (var gate = new Mutex(false, @"Local\ChatGPTRemote.Core." + UpdateFiles.TextDigest(directory.ToUpperInvariant())))
        {
            bool held;
            try { held = gate.WaitOne(30000); } catch (AbandonedMutexException) { held = true; }
            if (!held) throw new IOException("core_cache_busy");
            try
            {
                if (Directory.Exists(directory)) Verify(directory);
                else
                {
                    var stage = UpdateFiles.DirectoryChild(caches, ".install-" + Guid.NewGuid().ToString("N"));
                    try
                    {
                        using (var input = typeof(RemoteLauncher).Assembly.GetManifestResourceStream("ChatGPTRemote.Core.zip"))
                        using (var archive = new ZipArchive(input, ZipArchiveMode.Read))
                            foreach (var entry in archive.Entries)
                            {
                                var path = PayloadPath(stage, entry.FullName);
                                Directory.CreateDirectory(UpdateFiles.Extended(Path.GetDirectoryName(path)));
                                using (var source = entry.Open())
                                using (var output = new FileStream(UpdateFiles.Extended(path), FileMode.CreateNew, FileAccess.Write, FileShare.None))
                                { source.CopyTo(output); output.Flush(true); }
                            }
                        Verify(stage);
                        UpdateFiles.Write(UpdateFiles.Child(stage, "owner.json"), new Dictionary<string, object> {
                            { "schema", 1 }, { "app", AppId }, { "payload_id", PayloadId }
                        });
                        Directory.Move(UpdateFiles.Extended(stage), UpdateFiles.Extended(directory));
                    }
                    finally { if (Directory.Exists(stage)) UpdateFiles.RemoveTree(caches, stage); }
                }
            }
            finally { gate.ReleaseMutex(); }
        }
        return UpdateFiles.Child(directory, "ChatGPTRemote.Core.exe");
    }

    private static void Log(string root, string value)
    {
        try
        {
            var folder = UpdateFiles.DirectoryChild(UpdateFiles.DirectoryChild(root, "_wip"), "desktop-runtime");
            var path = UpdateFiles.Child(folder, "launcher.log");
            if (File.Exists(path) && new FileInfo(path).Length > 64000) File.Delete(path);
            File.AppendAllText(path, DateTime.Now.ToString("s") + " " + value + Environment.NewLine);
        }
        catch { }
    }

    private static int Run(string launcher, string root, string cache, string temp, string[] args, bool setup)
    {
        var core = Materialize(cache);
        var start = new ProcessStartInfo {
            FileName = core, Arguments = String.Join(" ", args.Select(UpdateFiles.Quote)),
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        UpdateFiles.CleanEnvironment(start);
        // A prewarmed window must acknowledge the tray launcher that owns an
        // update, while ordinary launches acquire their own public identity.
        var inherited = Environment.GetEnvironmentVariable("CHATGPTREMOTE_LAUNCHER_PID");
        start.EnvironmentVariables["CHATGPTREMOTE_LAUNCHER_PID"] = args.Contains("--window") && args.Contains("--update-ack") && !String.IsNullOrEmpty(inherited)
            ? inherited : Process.GetCurrentProcess().Id.ToString();
        start.EnvironmentVariables["CHATGPTREMOTE_LAUNCHER_EXE"] = launcher;
        start.EnvironmentVariables["CHATGPTREMOTE_PAYLOAD_ID"] = PayloadId;
        start.EnvironmentVariables["TEMP"] = temp; start.EnvironmentVariables["TMP"] = temp;
        if (setup) start.EnvironmentVariables["CHATGPTREMOTE_SETUP_RUNTIME"] = temp;
        else start.EnvironmentVariables.Remove("CHATGPTREMOTE_SETUP_RUNTIME");
        var job = CreateCoreJob();
        try
        {
            using (var process = Process.Start(start))
            {
                if (!AssignProcessToJobObject(job, process.Handle))
                {
                    process.Kill();
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                }
                if (!setup) Log(root, "core_started version=" + Version + " pid=" + process.Id);
                process.WaitForExit();
                if (!setup) Log(root, "core_exit code=" + process.ExitCode);
                return process.ExitCode;
            }
        }
        finally { CloseHandle(job); }
    }

    [STAThread]
    public static int Main(string[] args)
    {
        UpdateFiles.ResetDllDirectory();
        string root = null, runtime = null, owner = null, setupRun = null, setupOwner = null;
        try
        {
            var launcher = UpdateFiles.Full(typeof(RemoteLauncher).Assembly.Location);
            root = Path.GetDirectoryName(launcher);
            UpdateFiles.Plain(launcher);
            var installed = Installed(root);
            if (installed == null && !args.Contains("--self-test"))
            {
                var redirected = Redirect(root);
                if (redirected != null)
                {
                    Process.Start(new ProcessStartInfo {
                        FileName = redirected, Arguments = String.Join(" ", args.Select(UpdateFiles.Quote)),
                        WorkingDirectory = Path.GetDirectoryName(redirected), UseShellExecute = false, CreateNoWindow = true,
                        WindowStyle = ProcessWindowStyle.Hidden
                    });
                    return 0;
                }
                if (args.Contains("--agent")) throw new InvalidDataException("installation_required");
                setupOwner = UpdateFiles.DirectoryChild(Path.GetTempPath(), "ChatGPTRemote-Setup");
                setupRun = UpdateFiles.DirectoryChild(setupOwner, Guid.NewGuid().ToString("N"));
                var temp = UpdateFiles.DirectoryChild(setupRun, "temp");
                return Run(launcher, root, UpdateFiles.DirectoryChild(setupRun, "core"), temp, args, true);
            }
            var work = UpdateFiles.DirectoryChild(root, "_wip");
            owner = UpdateFiles.DirectoryChild(work, "run");
            runtime = UpdateFiles.DirectoryChild(owner, Guid.NewGuid().ToString("N"));
            return Run(launcher, root, UpdateFiles.DirectoryChild(work, "runtime"), runtime, args, false);
        }
        catch (Exception error)
        {
            if (root != null) Log(root, "launcher_failed type=" + error.GetType().Name + " reason=" + error.Message);
            if (!args.Contains("--self-test") && !args.Contains("--agent") && !args.Contains("--background"))
                MessageBox.Show("启动未完成。请查看程序文件夹 _wip/desktop-runtime 中的启动日志。", "ChatGPT Remote", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            if (runtime != null) { try { UpdateFiles.RemoveTree(owner, runtime); } catch { } }
            if (setupRun != null) { try { UpdateFiles.RemoveTree(setupOwner, setupRun); } catch { } }
        }
    }
}
