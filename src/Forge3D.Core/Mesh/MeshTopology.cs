namespace Forge3D.Core.Mesh;

/// <summary>Topologia de malhas de triângulos.</summary>
public static class MeshTopology
{
    /// <summary>
    /// Triângulos agrupados por peça conectada (vértices compartilhados). Uma
    /// malha de "detalhes agrupados" tem várias peças separadas; o Revit precisa
    /// de um conjunto de faces por peça para montar cada sólido fechado.
    /// </summary>
    public static List<List<int[]>> Components(IReadOnlyList<int[]> faces, int vertexCount)
    {
        var parent = new int[vertexCount];
        for (int i = 0; i < vertexCount; i++) parent[i] = i;
        int Find(int x)
        {
            while (parent[x] != x) x = parent[x] = parent[parent[x]];
            return x;
        }
        void Union(int a, int b)
        {
            a = Find(a);
            b = Find(b);
            if (a != b) parent[b] = a;
        }
        foreach (var f in faces)
        {
            Union(f[0], f[1]);
            Union(f[0], f[2]);
        }
        var groups = new Dictionary<int, List<int[]>>();
        var order = new List<List<int[]>>();
        foreach (var f in faces)
        {
            int root = Find(f[0]);
            if (!groups.TryGetValue(root, out var list))
            {
                groups[root] = list = new List<int[]>();
                order.Add(list);
            }
            list.Add(f);
        }
        return order;
    }
}
