# -*- coding: utf-8 -*-
"""mds —— 经典 MDS 降维

N×N 测地距离矩阵 → 8 维嵌入坐标。8D 欧氏距离 ≈ 3D 测地距离。

用稀疏特征值分解（eigsh）只算前 k 个分量，比密集 eigh 快一个数量级。
"""
import numpy as np
from scipy.sparse.linalg import eigsh
from scipy.linalg import eigh


def get_mds_embeddings_fast(distances, n_components=8):
    D_squared = distances ** 2
    row_means = np.mean(D_squared, axis=1)
    grand_mean = np.mean(row_means)
    B = -0.5 * (D_squared - row_means[:, np.newaxis] - row_means[np.newaxis, :] + grand_mean)
    try:
        eigenvalues, eigenvectors = eigsh(B, k=n_components, which='LA')
        eigenvalues = eigenvalues[::-1]
        eigenvectors = eigenvectors[:, ::-1]
    except Exception:
        eigenvalues, eigenvectors = eigh(B)
        idx = np.argsort(eigenvalues)[::-1]
        eigenvalues = eigenvalues[idx][:n_components]
        eigenvectors = eigenvectors[:, idx][:, :n_components]
    eigenvalues = np.maximum(eigenvalues, 0)
    return eigenvectors * np.sqrt(eigenvalues)