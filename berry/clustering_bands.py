import os
import sys
import logging

import numpy as np

from berry import log
from berry._subroutines.clustering_libs import MATERIAL

try:
    import berry._subroutines.loaddata as d
except:
    pass


def run_clustering(max_band: int, min_band: int = 0, tol: float = 0.95, npr: int = 1, logger_name: str = "cluster", logger_level: int = logging.INFO, flush: bool = False):
    logger = log(logger_name, "CLUSTER", level=logger_level, flush=flush)

    logger.header()

    ###########################################################################
    # 1. DEFINING THE CONSTANTS
    ###########################################################################
    OUTPUT_PATH = ''
    max_band = max_band if max_band != -1 else d.nbnd-1

    ###########################################################################
    # 2. STDOUT THE PARAMETERS
    ########################################################################### 
    if OUTPUT_PATH != "" and not os.path.exists(OUTPUT_PATH):
        os.mkdir(OUTPUT_PATH)
        logger.warning(f'The {OUTPUT_PATH} was created.')

    logger.info(f'\tMin band: {min_band}    Max band: {max_band}')
    logger.info(f'\tTolerance: {tol}')
    logger.info(f'\tNumber of CPUs: {npr}\n')

    logger.info(f"\tUnique reference of run: {d.refname}")
    logger.info(f"\tDirectory where the wfc are: {d.wfcdirectory}")
    logger.info(f"\tNumber of k-points in each direction: {d.nkx}, {d.nky}, {d.nkz}")
    logger.info(f"\tTotal number of k-points: {d.nks}")
    logger.info(f"\tNumber of bands: {d.nbnd}\n")
    logger.info("\tNeighbors loaded")
    logger.info("\tEigenvalues loaded")

    connections = np.load(os.path.join(d.workdir, "dp.npy"))
    logger.info("\tModulus of direct product loaded\n")

    logger.info("\tFinished reading data\n")

    ###########################################################################
    # 3. CLUSTERING ALGORITHM
    ########################################################################### 

    material = MATERIAL(d.nkx, d.nky, d.nbnd, d.nks, d.eigenvalues,
                        connections, d.neighbors, logger, n_process=npr)

    logger.info('\tCalculating Vectors')
    material.make_vectors(min_band=min_band, max_band=max_band)

    logger.info('\n\tCalculating Connections')
    material.make_connections(tol=tol)

    logger.info('\tSolving problem')
    material.solve()

    logger.info('\n\tClustering Done')

    with open(os.path.join(d.workdir, 'final.report'), 'w') as f:
        f.write(material.final_report)

    with open(os.path.join(d.workdir, 'bandsfinal.npy'), 'wb') as f:
        np.save(f, material.bands_final)

    with open(os.path.join(d.workdir, 'signalfinal.npy'), 'wb') as f:
        np.save(f, material.signal_final)

    with open(os.path.join(d.workdir, 'correct_signalfinal.npy'), 'wb') as f:
        np.save(f, material.correct_signalfinal)

    with open(os.path.join(d.workdir, 'degeneratefinal.npy'), 'wb') as f:
        np.save(f, material.degenerate_final)

    with open(os.path.join(d.workdir, 'final_score.npy'), 'wb') as f:
        np.save(f, material.final_score)

    sys.stdout.write('\n')
    logger.footer()

if __name__ == "__main__":
    run_clustering(9, log("clustering", "CLUSTERING", "version", logging.DEBUG))