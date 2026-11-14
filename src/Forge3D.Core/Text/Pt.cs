namespace Forge3D.Core.Text;

/// <summary>Textos e listas em português usados pela interface.</summary>
public static class Pt
{
    public const string ProductName = "Forge 3D";

    public sealed record Choice(string Value, string Label);

    public static readonly IReadOnlyList<Choice> Categories = new[]
    {
        new Choice("Furniture", "Mobiliário"),
        new Choice("FurnitureSystems", "Sistemas de mobiliário"),
        new Choice("Casework", "Marcenaria (casework)"),
        new Choice("LightingFixtures", "Luminárias"),
        new Choice("Planting", "Vegetação"),
        new Choice("Entourage", "Entourage"),
        new Choice("SpecialtyEquipment", "Equipamento especializado"),
        new Choice("PlumbingFixtures", "Peças hidrossanitárias"),
        new Choice("Site", "Implantação (site)"),
        new Choice("Parking", "Estacionamento"),
        new Choice("MechanicalEquipment", "Equipamento mecânico"),
        new Choice("ElectricalEquipment", "Equipamento elétrico"),
        new Choice("ElectricalFixtures", "Dispositivos elétricos"),
        new Choice("FoodServiceEquipment", "Equipamento de cozinha"),
        new Choice("MedicalEquipment", "Equipamento médico"),
        new Choice("Signage", "Sinalização"),
        new Choice("GenericModel", "Modelo genérico"),
    };

    public static readonly IReadOnlyList<Choice> Levels = new[]
    {
        new Choice("C", "Nível C — parâmetros com significado (espessuras, recuos, diâmetros)"),
        new Choice("B", "Nível B — tudo proporcional ao envelope"),
        new Choice("A", "Nível A — geometria fixa"),
    };

    public static readonly IReadOnlyList<Choice> Units = new[]
    {
        new Choice("auto", "Automática"), new Choice("mm", "Milímetros"), new Choice("cm", "Centímetros"),
        new Choice("m", "Metros"), new Choice("in", "Polegadas"), new Choice("ft", "Pés"),
    };

    public static readonly IReadOnlyList<Choice> UpAxes = new[]
    {
        new Choice("auto", "Automático"), new Choice("Z", "Z para cima"), new Choice("Y", "Y para cima"),
        new Choice("-Z", "-Z para cima"), new Choice("-Y", "-Y para cima"), new Choice("X", "X para cima"),
    };

    public static readonly IReadOnlyList<Choice> ImageStrategies = new[]
    {
        new Choice("auto", "Automática"),
        new Choice("revolucao", "Revolução (vasos, postes, luminárias, colunas)"),
        new Choice("extrusao", "Extrusão da silhueta (painéis, placas, recortes)"),
        new Choice("ia", "Reconstrução 3D por IA"),
    };

    public static string CategoryLabel(string value) =>
        Categories.FirstOrDefault(c => c.Value == value)?.Label ?? value;

    public static string LevelLabel(string level) => level switch
    {
        "C" => "Nível C",
        "B" => "Nível B",
        "A" => "Nível A",
        _ => level,
    };

    public static readonly string[] ModelExtensions =
        { ".skp", ".max", ".fbx", ".3ds", ".obj", ".stl", ".ply", ".off", ".glb", ".gltf", ".dae", ".3mf", ".stp", ".step", ".3dm", ".sat" };

    public static readonly string[] ImageExtensions = { ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff" };

    public const string ModelFileFilter =
        "Modelos 3D|*.skp;*.max;*.fbx;*.3ds;*.obj;*.stl;*.ply;*.off;*.glb;*.gltf;*.dae;*.3mf;*.stp;*.step;*.3dm;*.sat|" +
        "SketchUp (*.skp)|*.skp|3ds Max (*.max)|*.max|FBX (*.fbx)|*.fbx|OBJ (*.obj)|*.obj|Todos os arquivos (*.*)|*.*";

    public const string ImageFileFilter = "Imagens|*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff|Todos os arquivos (*.*)|*.*";
}
