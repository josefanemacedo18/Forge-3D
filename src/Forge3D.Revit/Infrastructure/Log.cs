using System.Text;
using Forge3D.Core.Settings;

namespace Forge3D.Revit.Infrastructure;

/// <summary>Registro do plugin em %LOCALAPPDATA%\Forge3D\logs\plugin.log.</summary>
internal static class Log
{
    private static readonly object Lock = new();

    public static string FilePath => Path.Combine(Forge3DSettings.LogDirectory, "plugin.log");

    public static void Info(string message) => Write("INFO", message);

    public static void Warn(string message) => Write("AVISO", message);

    public static void Error(string message, Exception? ex = null) =>
        Write("ERRO", ex is null ? message : $"{message}{Environment.NewLine}{ex}");

    private static void Write(string level, string message)
    {
        try
        {
            lock (Lock)
            {
                Directory.CreateDirectory(Forge3DSettings.LogDirectory);
                var fi = new FileInfo(FilePath);
                if (fi.Exists && fi.Length > 4_000_000)
                {
                    File.Copy(FilePath, FilePath + ".1", true);
                    File.WriteAllText(FilePath, "");
                }
                File.AppendAllText(FilePath, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {level} {message}{Environment.NewLine}", Encoding.UTF8);
            }
        }
        catch
        {
            // O registro nunca pode derrubar o plugin.
        }
    }
}
