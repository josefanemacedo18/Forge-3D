using Forge3D.Core.Engine;
using Forge3D.Core.Settings;

namespace Forge3D.Revit.Infrastructure;

/// <summary>
/// Mantém um único processo do motor durante a sessão do Revit (carregar o
/// Python e as bibliotecas leva alguns segundos; as conversões seguintes são
/// imediatas). Reinicia sozinho se o processo cair.
/// </summary>
internal static class EngineHost
{
    private static readonly object Lock = new();
    private static EngineClient? _client;
    private static string? _signature;

    public static string AddinDirectory => Path.GetDirectoryName(typeof(EngineHost).Assembly.Location)!;

    public static EngineClient Get(Forge3DSettings settings)
    {
        lock (Lock)
        {
            var info = EngineLocator.StartInfo(settings, AddinDirectory);
            var signature = $"{info.PythonPath}|{info.EngineDirectory}|{info.ModelsDirectory}";
            if (_client is not null && _client.IsRunning && signature == _signature) return _client;
            _client?.Dispose();
            _client = new EngineClient(info);
            _signature = signature;
            Log.Info($"Motor: python='{info.PythonPath}' pasta='{info.EngineDirectory}'.");
            return _client;
        }
    }

    public static void Shutdown()
    {
        lock (Lock)
        {
            try
            {
                _client?.Dispose();
            }
            catch (Exception ex)
            {
                Log.Warn($"Falha ao encerrar o motor: {ex.Message}");
            }
            _client = null;
            _signature = null;
        }
    }
}
