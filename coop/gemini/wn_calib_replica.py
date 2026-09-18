"""
wn_calib_replica.py - Réplica independiente de calibración de WeatherNext 3
Astroweather Cabraspace (coop/gemini)
"""

import csv
import json
import math
import os
import sys
import numpy as np
import scipy.optimize
import scipy.special
import scipy.stats

# Rutas absolutas y relativas
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOP_DIR = os.path.dirname(SCRIPT_DIR)
CSV_PATH = os.path.join(COOP_DIR, "datos", "wn_calibracion.csv")
JSON_PATH = os.path.join(SCRIPT_DIR, "resultado_replica.json")

def load_and_filter_data(csv_path):
    rows = []
    wn_cols = ['wn_mean', 'wn_p10', 'wn_p25', 'wn_p50', 'wn_p75', 'wn_p90']
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            # lead_day entre 1 y 5 inclusive
            try:
                ld = int(r['lead_day'])
            except (ValueError, TypeError):
                continue
            if ld < 1 or ld > 5:
                continue
            
            # ecmwf no vacio y finito
            ec_str = r['ecmwf'].strip()
            if not ec_str:
                continue
            try:
                ec_val = float(ec_str)
                if not math.isfinite(ec_val):
                    continue
            except ValueError:
                continue
            
            # las seis columnas wn_* finitas
            valid_wn = True
            for c in wn_cols:
                v_str = r[c].strip()
                if not v_str:
                    valid_wn = False
                    break
                try:
                    v_val = float(v_str)
                    if not math.isfinite(v_val):
                        valid_wn = False
                        break
                except ValueError:
                    valid_wn = False
                    break
            if not valid_wn:
                continue
            
            rows.append(r)
            
    return rows

def fit_logistic(X, y_vec):
    """
    Regresión logística por máxima verosimilitud SIN regularización.
    Optimización por Newton-CG con gradiente y Hessiano analíticos.
    """
    def loss_grad(b):
        z = X @ b
        nll = np.sum(np.logaddexp(0, z) - y_vec * z)
        p = scipy.special.expit(z)
        grad = X.T @ (p - y_vec)
        return nll, grad

    def hess(b):
        z = X @ b
        p = scipy.special.expit(z)
        w = p * (1.0 - p)
        return (X.T * w) @ X

    b0 = np.zeros(X.shape[1])
    res = scipy.optimize.minimize(loss_grad, b0, jac=True, hess=hess, method='Newton-CG', tol=1e-8)
    return res.x

def compute_metrics(y_true, y_pred, y_clim):
    n = int(len(y_true))
    brier = float(np.mean((y_pred - y_true)**2))
    brier_clim = float(np.mean((y_clim - y_true)**2))
    bss = float(1.0 - brier / brier_clim) if brier_clim > 0 else 0.0
    eps = 1e-15
    p_clipped = np.clip(y_pred, eps, 1.0 - eps)
    log_loss = float(-np.mean(y_true * np.log(p_clipped) + (1.0 - y_true) * np.log(1.0 - p_clipped)))
    
    # AUC con empates promediados
    ranks = scipy.stats.rankdata(y_pred, method='average')
    n1 = np.sum(y_true == 1)
    n0 = np.sum(y_true == 0)
    if n1 > 0 and n0 > 0:
        u = np.sum(ranks[y_true == 1]) - n1 * (n1 + 1.0) / 2.0
        auc = float(u / (n1 * n0))
    else:
        auc = float('nan')
        
    return {
        "n": n,
        "brier": brier,
        "bss": bss,
        "log_loss": log_loss,
        "auc": auc
    }

def run_pipeline():
    print(f"Leyendo datos desde: {CSV_PATH}")
    rows = load_and_filter_data(CSV_PATH)
    n_rows = len(rows)
    unique_nights = sorted(list(set(r['night'] for r in rows)))
    unique_months = sorted(list(set(r['month'] for r in rows)))
    
    filtro = {
        "filas": n_rows,
        "noches": len(unique_nights),
        "meses": len(unique_months)
    }
    print(f"Filtro aplicado: {filtro['filas']} filas, {filtro['noches']} noches, {filtro['meses']} meses")
    
    # Extracción de arrays
    y = np.array([float(r['clear']) for r in rows], dtype=float)
    ecmwf = np.array([float(r['ecmwf']) / 100.0 for r in rows], dtype=float)
    wn_mean = np.array([float(r['wn_mean']) / 100.0 for r in rows], dtype=float)
    wn_p10 = np.array([float(r['wn_p10']) / 100.0 for r in rows], dtype=float)
    wn_p90 = np.array([float(r['wn_p90']) / 100.0 for r in rows], dtype=float)
    wn_spread = wn_p90 - wn_p10
    lead_day = np.array([float(r['lead_day']) for r in rows], dtype=float)
    months = np.array([r['month'] for r in rows])
    nights = np.array([r['night'] for r in rows])
    
    # E_control: wn_mean permutado una vez antes de la validación
    rng0 = np.random.default_rng(0)
    wn_mean_perm = rng0.permutation(wn_mean)
    
    # Matrices de diseño
    ones = np.ones(n_rows, dtype=float)
    X_dict = {
        "A_ecmwf": np.column_stack([ones, ecmwf, lead_day]),
        "B_wn": np.column_stack([ones, wn_mean, lead_day]),
        "C_wn_spread": np.column_stack([ones, wn_mean, wn_spread, lead_day]),
        "D_blend": np.column_stack([ones, wn_mean, ecmwf, lead_day]),
        "E_control": np.column_stack([ones, wn_mean_perm, ecmwf, lead_day])
    }
    
    # Validación Deja-un-mes-fuera
    preds = {m: np.zeros(n_rows, dtype=float) for m in X_dict}
    p_clim = np.zeros(n_rows, dtype=float)
    
    print("Iniciando validación Leave-One-Month-Out...")
    for m in unique_months:
        train_mask = (months != m)
        test_mask = (months == m)
        
        # Climatología del pliegue = tasa de clear en meses de entrenamiento
        clim_val = np.mean(y[train_mask])
        p_clim[test_mask] = clim_val
        
        for model_name, X_mat in X_dict.items():
            X_train = X_mat[train_mask]
            y_train = y[train_mask]
            X_test = X_mat[test_mask]
            
            beta = fit_logistic(X_train, y_train)
            preds[model_name][test_mask] = scipy.special.expit(X_test @ beta)
            
    # Cálculo de métricas
    candidates = ["A_ecmwf", "B_wn", "C_wn_spread", "D_blend", "E_control"]
    metricas = {}
    
    for cand in candidates:
        cand_global = compute_metrics(y, preds[cand], p_clim)
        por_lead = {}
        for ld in range(1, 6):
            ld_mask = (lead_day == float(ld))
            por_lead[str(ld)] = compute_metrics(y[ld_mask], preds[cand][ld_mask], p_clim[ld_mask])
            
        metricas[cand] = {
            "global": cand_global,
            "por_lead": por_lead
        }
        print(f"[{cand}] Global: Brier={cand_global['brier']:.4f}, BSS={cand_global['bss']:.4f}, LogLoss={cand_global['log_loss']:.4f}, AUC={cand_global['auc']:.4f}")

    # Bootstrap POR NOCHES (2000 rondas, default_rng(11))
    print("Iniciando Bootstrap por noches (2000 rondas, seed 11)...")
    # Mapeo de noches a índices de filas
    night_to_indices = {}
    for idx, night_str in enumerate(nights):
        if night_str not in night_to_indices:
            night_to_indices[night_str] = []
        night_to_indices[night_str].append(idx)
        
    unique_nights_arr = np.array(unique_nights)
    n_nights = len(unique_nights_arr)
    
    # Precomputar suma de errores cuadráticos por noche para acelerar
    # Para cada noche j: SE_night[cand][j] = sum_{i in night} (pred - y)^2, n_night[j] = len(night)
    se_by_night = {cand: np.zeros(n_nights, dtype=float) for cand in candidates}
    count_by_night = np.zeros(n_nights, dtype=float)
    
    for j, n_str in enumerate(unique_nights_arr):
        idx_list = night_to_indices[n_str]
        y_n = y[idx_list]
        count_by_night[j] = len(idx_list)
        for cand in candidates:
            p_n = preds[cand][idx_list]
            se_by_night[cand][j] = np.sum((p_n - y_n)**2)
            
    # Bootstrap rounds
    rng11 = np.random.default_rng(11)
    n_boot = 2000
    # Generamos los índices de noches muestreadas
    boot_night_indices = rng11.choice(n_nights, size=(n_boot, n_nights), replace=True)
    
    # Sumar sobre las noches muestreadas para cada ronda
    boot_counts = np.sum(count_by_night[boot_night_indices], axis=1) # (2000,)
    boot_brier = {}
    for cand in candidates:
        boot_se = np.sum(se_by_night[cand][boot_night_indices], axis=1) # (2000,)
        boot_brier[cand] = boot_se / boot_counts
        
    pairs = [
        ("B-A", "B_wn", "A_ecmwf"),
        ("D-A", "D_blend", "A_ecmwf"),
        ("D-B", "D_blend", "B_wn"),
        ("C-B", "C_wn_spread", "B_wn"),
        ("E-A", "E_control", "A_ecmwf")
    ]
    
    bootstrap_results = {}
    for pair_name, m1, m2 in pairs:
        pt_diff = metricas[m1]["global"]["brier"] - metricas[m2]["global"]["brier"]
        boot_diffs = boot_brier[m1] - boot_brier[m2]
        p2_5 = float(np.percentile(boot_diffs, 2.5))
        p97_5 = float(np.percentile(boot_diffs, 97.5))
        frac_neg = float(np.mean(boot_diffs < 0.0))
        bootstrap_results[pair_name] = {
            "punto": float(pt_diff),
            "p2_5": p2_5,
            "p97_5": p97_5,
            "frac_neg": frac_neg
        }
        print(f"Bootstrap [{pair_name}]: Punto={pt_diff:+.5f}, CI95=[{p2_5:+.5f}, {p97_5:+.5f}], Frac<0={frac_neg:.4f}")

    # Tabla de fiabilidad de B_wn y D_blend (10 bins iguales en [0, 1])
    print("Calculando fiabilidad (10 bins en [0, 1])...")
    fiabilidad = {}
    for cand in ["B_wn", "D_blend"]:
        p_cand = preds[cand]
        bin_list = []
        for b in range(10):
            low = b / 10.0
            high = (b + 1) / 10.0
            if b == 9:
                mask = (p_cand >= low) & (p_cand <= high)
            else:
                mask = (p_cand >= low) & (p_cand < high)
            cnt = int(np.sum(mask))
            if cnt >= 20:
                p_mean = float(np.mean(p_cand[mask]))
                y_obs = float(np.mean(y[mask]))
                bin_list.append([p_mean, y_obs, cnt])
        fiabilidad[cand] = bin_list

    # Coeficientes de B_wn y D_blend con TODOS los datos filtrados
    print("Ajustando coeficientes con todos los datos filtrados...")
    beta_B = fit_logistic(X_dict["B_wn"], y)
    beta_D = fit_logistic(X_dict["D_blend"], y)
    
    coeficientes = {
        "B_wn": {
            "intercept_a": float(beta_B[0]),
            "b_wn_mean": float(beta_B[1]),
            "c_lead_day": float(beta_B[2]),
            "formula": "z = a + b*wn_mean + c*L",
            "beta": [float(x) for x in beta_B]
        },
        "D_blend": {
            "intercept_a": float(beta_D[0]),
            "b_wn_mean": float(beta_D[1]),
            "c_ecmwf": float(beta_D[2]),
            "d_lead_day": float(beta_D[3]),
            "formula": "z = a + b*wn_mean + c*ecmwf + d*L",
            "beta": [float(x) for x in beta_D]
        }
    }
    print(f"Coefs B_wn: {coeficientes['B_wn']['beta']}")
    print(f"Coefs D_blend: {coeficientes['D_blend']['beta']}")

    # Guardar resultado en JSON
    output_json = {
        "filtro": filtro,
        "metricas": metricas,
        "bootstrap": bootstrap_results,
        "fiabilidad": fiabilidad,
        "coeficientes": coeficientes
    }
    
    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(output_json, f, indent=2, ensure_ascii=False)
        
    print(f"Resultados guardados exitosamente en {JSON_PATH}")

if __name__ == "__main__":
    run_pipeline()
