"""This module contains the algorithm behind band classification.
It determines which points belong to each band used for posterior calculations.
The algorithm uses machine learning techniques to cluster the data.
"""

from array import array
import os
import numpy as np
import networkx as nx
from log_libs import log
from loaddata import version
from functools import partial
from scipy.ndimage import correlate
from multiprocessing import Process
from multiprocessing import Manager
from scipy.optimize import curve_fit
from contatempo import time_fn
from write_k_points import _bands_numbers
from typing import Tuple, Union, Callable


###########################################################################
# Type Definition
###########################################################################
Kpoint = int
Connection = float
Band = int

###########################################################################
# Constant Definition
###########################################################################
CORRECT = 5
POTENTIAL_CORRECT = 4
POTENTIAL_MISTAKE = 3
DEGENERATE = 2
MISTAKE = 1
NOT_SOLVED = 0

N_NEIGS = 4

LOG = log('clustering', 'Band Clustering', version)

def evaluate_result(values: Union[list[Connection], np.ndarray]) -> int:
    '''
    This function attributes the correspondent signal using
    the dot product between each neighbor.

    Parameters
        values: array_like
            It is an array that contains the dot product
            between the k point and all neighbors.

    Returns
        signal: int
            C -> Mean connection of each k point
            Value :                              Description
            0     :                        The point is not solved
            1     :  MISTAKE               C <= 0.2
            2     :  DEGENERATE            It is a degenerate point.
            3     :  POTENTIAL_MISTAKE     C <= 0.8
            4     :  POTENTIAL_CORRECT     0.8 < C < 0.9
            5     :  CORRECT               C > 0.9
    '''

    TOL = 0.9       # Tolerance for CORRECT output
    TOL_DEG = 0.8   # Tolerance for POTENTIAL_CORRECT output
    TOL_MIN = 0.2   # Tolerance for POTENTIAL_MISTAKE output

    value = np.mean(values) # Mean conection of each k point

    if value > TOL:
        return CORRECT

    if value > TOL_DEG:
        return POTENTIAL_CORRECT

    if value > TOL_MIN and value < TOL_DEG:
        return POTENTIAL_MISTAKE

    return MISTAKE

def evaluate_point(k: Kpoint, bn: Band, k_index: np.ndarray, k_matrix: np.ndarray,
                   signal: np.ndarray, bands: np.ndarray, energies: np.ndarray) -> Tuple[int, list[int]]:
    '''
    Assign a signal value depending on energy continuity.

    Parameters
        k: Kpoint
            Integer that index the k point on analysis.
        bn: Band
            Integer that index the band number on analysis.
        k_index: array_like
            An array that contains the indices of each k point on the k-space matrix.
        k_matrix: array_like
            An array with the shape of the k-space. It contains the value of each k point in their corresponding position.
        signal: array_like
            An array with the current signal value for each k point.
        bands: array_like
            An array with the information of current solution of band clustering.
        energies: array_like
            It contais the energy value for each k point.
    
    Returns
        (signal, scores): Tuple[int, list[int]]
            scores: list[int]
                Sinalize if exist continuity on each direction [Down, Right, up, Left].
                    1 --- This direction preserves energy continuity.
                    0 --- This direction does not preserves energy continuity.
            N -> Number of directions with energy continuity.
            signal: int
                Value :                              Description
                0     :                        The point is not solved
                1     :  MISTAKE               N = 0
                2     :  DEGENERATE            It is a degenerate point.
                3     :  OTHER                 0 < N < 4
                4     :  CORRECT               N = 4
    '''
    
    CORRECT = 4
    MISTAKE = 1
    OTHER = 3

    TOL = 0.9         # Tolerance to consider that exist energy continuity
    N = 4             # Number of points to fit the curve

    mach_bn = bands[k, bn]          # original band
    sig = signal[k, bn]             # signal
    ik, jk = k_index[k]             # k point indices on k-space
    Ek = energies[k, mach_bn]       # k point's Energy value

    def difference_energy(Ek: float, Enew: float) -> float:
        '''
        Attributes a value that score how close is Ek to Enew.

        Parameters
            Ek: float
                K point's energy value.
            Enew: float
                Energy value to compare.
        Returns
            score: float [0, 1]
                Value that measures the closeness between Ek and Enew consider the other possible values.
        '''
        min_energy = np.min(np.abs(Enew-energies[k]))           # Computes all possible energy values for this k point
        delta_energy = np.abs(Enew-Ek)                          # Actual difference between Ek and Enew
        return min_energy/delta_energy if delta_energy else 1   # Score

    directions = np.array([[1,0], [0,1], [-1,0], [0,-1]])       # Down, Right, Up, Left
    energy_vals = []

    ###########################################################################
    # Calculate the score for each direction
    ###########################################################################

    for direction in directions:
        # Iterates each direction and obtain N points to be used for fit the curve
        n = np.repeat(np.arange(1,N+1),2).reshape(N,2)
        kn_index = n*direction + np.array([ik, jk])
        i, j = kn_index[:, 0], kn_index[:, 1]   # Selects the indices of these N points
        flag = len(np.unique(i)) > 1            # Necessary to identify which will be the direction of the fit
        if flag:
            i = i[i >= 0]
            i = i[i < k_matrix.shape[0]]
            j = np.full(len(i), j[0])
        else:
            j = j[j >= 0]
            j = j[j < k_matrix.shape[1]]
            i = np.full(len(j), i[0])
        
        ks = k_matrix[i, j] if len(i) > 0 else []   # Identify the N k points
        if len(ks) == 0:    
            # The direction in analysis does not have points
            energy_vals.append(1)
            continue
        if len(ks) <= 3:    
            # If there are not enough points to fit the curve it is used the Energy of the nearest neighbor
            Eneig = energies[ks[0], bands[ks[0], bn]]
            energy_vals.append(difference_energy(Ek, Eneig))
            continue
        
        k_bands = bands[ks, bn]
        Es = energies[ks, k_bands]
        X = i if flag else j
        new_x = ik if flag else jk
        pol = lambda x, a, b, c: a*x**2 + b*x + c           # Second order polynomial
        popt, pcov = curve_fit(pol, X, Es)                  # Curve fitting
        Enew = pol(new_x, *popt)                            # Obtain Energy value
        energy_vals.append(difference_energy(Ek, Enew))     # Calculate score
    
    energy_vals = np.array(energy_vals)
    scores = (energy_vals > TOL)*1  # Verification energy continuity on each direction
    score = np.sum(scores)          # Counting how many directions preserves energy continuity
    
    if score == N_NEIGS:
        return CORRECT, scores
    if score == 0:
        return MISTAKE, scores
    return OTHER, scores


class MATERIAL:
    '''
    This object contains all information about the material that
    will be used to solve their bands' problem.

    Atributes
        nkx : int
            The number ok k points on x direction.
        nky : int
            The number ok k points on y direction.
        nbnb : int
            Total number of bands.
        total_bands : int
            Total number of bands.
        nks : int
            Total number of k points.
        eigenvalues : array_like
            It contains the energy value for each k point.
        connections : array_like
            The dot product information between k points.
        neighbors : array_like
            An array with the information about which are the neighbors of each k point.
        vectors : array_like
            Each k point in the vector representation on k-space.
        n_process : int
            Number of processes to use.
        bands_final : array_like
            An array with final result of bands attribution.
        signal_final : array_like
            Contains the resulting signal for each k point.
        final_score : aray_like
            It contains the result score for each band.

    Methods
        solve() : None
            This method is the main algorithm which iterates between solutions
                trying to find the best result for the material.
        make_vectors() : None
            It transforms the information into more convenient data structures.
        make_BandsEnergy() : array_like
            It sets the energy information in more convinient data structure
        make_kpointsIndex() : None
            It computes the indices of each k point in their correspondence in k-space.
        make_connections() : None
            This function evaluates the connection between each k point, and adds an edge
                to the graph if its connection is greater than a tolerance value (tol).
        get_neigs() : list[Kpoint]
            Obtain the i's neighbors.
        find_path() : bool
            Verify if exist a path between two k points inside the graph.
    '''
    def __init__(self, nkx: int, nky: int, nbnd: int, nks: int, eigenvalues: np.ndarray,
                 connections: np.ndarray, neighbors: np.ndarray, n_process: int=1) -> None:
        '''
        Initialize the object.

        Parameters
            nkx : int
                The number ok k points on x direction.
            nky : int
                The number ok k points on y direction.
            nbnb : int
                Total number of bands.
            total_bands : int
                Total number of bands.
            nks : int
                Total number of k points.
            eigenvalues : array_like
                It contains the energy value for each k point.
            connections : array_like
                The dot product information between k points.
            neighbors : array_like
                An array with the information about which are the neighbors of each k point.
            vectors : array_like
                Each k point in the vector representation on k-space.
            n_process : int
                Number of processes to use.
        '''
        self.nkx = nkx
        self.nky = nky
        self.nbnd = nbnd
        self.total_bands = nbnd
        self.nks = nks
        self.eigenvalues = eigenvalues
        self.connections = connections
        self.neighbors = neighbors
        self.vectors = None
        self.n_process = n_process

    def make_BandsEnergy(self) -> np.ndarray:
        '''
        It sets the energy information in more convinient data structure
        
        Parameters
            None
        
        Returns
            BandsEnergy : array_like
                An array with the information about each energy value on k-space.
        '''
        bands_final, _ = np.meshgrid(np.arange(0, self.nbnd),
                                     np.arange(0, self.nks))
        BandsEnergy = np.empty((self.nbnd, self.nkx, self.nky), float)
        for bn in range(self.nbnd):
            count = -1
            zarray = np.empty((self.nkx, self.nky), float)
            for j in range(self.nky):
                for i in range(self.nkx):
                    count += 1
                    zarray[i, j] = self.eigenvalues[count,
                                                    bands_final[count, bn]]
            BandsEnergy[bn] = zarray
        return BandsEnergy

    def make_kpointsIndex(self) -> None:
        '''
        It computes the indices of each k point in their correspondence in k-space.
        '''
        My, Mx = np.meshgrid(np.arange(self.nky), np.arange(self.nkx))
        self.matrix = My*self.nkx+Mx
        counts = np.arange(self.nks)
        self.kpoints_index = np.stack([counts % self.nkx, counts//self.nkx],
                                      axis=1)

    @time_fn(prefix="\t")
    def make_vectors(self, min_band: int=0, max_band: int=-1) -> None:
        '''
        It transforms the information into more convenient data structures.

        Parameters
            min_band : int
                An integer that gives the minimum band that clustering will use.
                    default: 0
            max_band : int
                An integer that gives the maximum band that clustering will use.
                    default: All

        Result
            self.vectors: [kx_b, ky_b, E_b]
                k = (kx, ky)_b: k point
                b: band number
            self.degenerados: It marks the degenerate points
            self.GRPAH: It is a graph in which each node represents a vector.
            self.energies: It contains the energy values for each band distributed
                        in a matrix.
        '''
        process_name = 'Making Vectors'
        LOG.percent_complete(0, 100, title=process_name)

        ###########################################################################
        # Compute the auxiliar information
        ###########################################################################
        self.GRAPH = nx.Graph()     # Create the initail Graph
        self.min_band = min_band
        self.max_band = max_band
        nbnd = self.nbnd if max_band == -1 else max_band+1
        self.make_kpointsIndex()
        energies = self.make_BandsEnergy()
        LOG.percent_complete(20, 100, title=process_name)

        ###########################################################################
        # Compute the vector representation of each k point
        ###########################################################################
        n_vectors = (nbnd-min_band)*self.nks
        ik = np.tile(self.kpoints_index[:, 0], nbnd-min_band)
        jk = np.tile(self.kpoints_index[:, 1], nbnd-min_band)
        bands = np.arange(min_band, nbnd)
        eigenvalues = self.eigenvalues[:, bands].T.reshape(n_vectors)
        self.vectors = np.stack([ik, jk, eigenvalues], axis=1)
        LOG.percent_complete(100, 100, title=process_name)

        self.GRAPH.add_nodes_from(np.arange(n_vectors))     # Add the nodes, each node represent a k point
        
        ###########################################################################
        # Verify if any k point is a degenerate point
        ###########################################################################
        self.degenerados = []
        def obtain_degenerates(vectors: np.ndarray) -> list[Kpoint]:
            '''
            Find all degenerate k points present on vectors.

            Parameters
                vectors : array_like
                    An array with vector representation of k points.
            
            Returns
                degenerates : list[Kpoint]
                    It contains the degenerate points found.
            '''
            degenerates = []
            for i, v in vectors:
                degenerado = np.where(np.all(np.isclose(self.vectors[i+1:]-v, 0),
                                    axis=1))[0] # Verify which points have numerically the same value
                if len(degenerado) > 0:
                    LOG.debug(f'Found degenerete point for {i}')
                    degenerates += [[i, d+i+1] for d in degenerado]
            return degenerates

        # Parallelize the verification process
        self.degenerados = self.parallelize('Finding degenerate points', obtain_degenerates, enumerate(self.vectors))

        if len(self.degenerados) > 0:
            LOG.info('Degenerate Points: ')
            for d in self.degenerados:
                LOG.info(f'\t{d}')

        self.ENERGIES = energies
        self.nbnd = nbnd-min_band
        self.bands_final = np.full((self.nks, self.total_bands), -1, dtype=int)

    def get_neigs(self, i: Kpoint) -> list[Kpoint]:
        '''
        Obtain the i's neighbors

        Parameters
            i : Kpoint
                The node index.
        
        Returns
            neighbors : list[Kpoint]
                List with the nodes that are neighbors of the node i.
        '''
        return list(self.GRAPH.neighbors(i))

    def find_path(self, i: Kpoint, j:Kpoint) -> bool:
        '''
        Verify if exist a path between two k points inside the graph

        Parameters
            i : Kpoint
            j : Kpoint
        
        Returns : bool
            If exists a path return True
        '''
        neighs = self.get_neigs(i)
        neigh = neighs.pop(0) if len(neighs) > 0 else None
        visited = [i] + [d for points in self.degenerados
                         for d in points if d not in [i, j]]
        while (neigh is not None and neigh != j and
               (neigh not in visited or len(neighs) > 0)):
            if neigh in visited:
                neigh = neighs.pop(0)
                continue
            visited.append(neigh)
            for k in self.get_neigs(neigh):
                if k not in visited:
                    neighs.append(k)
            neigh = neighs.pop(0) if len(neighs) > 0 else None
        return neigh == j if neigh is not None else False

    @time_fn(prefix="\t")
    def make_connections(self, tol:float=0.95) -> None:
        '''
        This function evaluates the connection between each k point,
        and adds an edge to the graph if its connection is greater
        than a tolerance value (tol).

        <i|j>: The dot product between i and j represents its connection

        Parameters
            tol : float
                It is the minimum connection value that will be accepted as an edge.
                default: 0.95
        '''
        ###########################################################################
        # Find the edges on the graph
        ###########################################################################
        def connection_component(vectors:np.ndarray) -> list[list[Kpoint]]:
            '''
            Find the possible edges in the graph using the information of dot product.

            Parameters
                vectors : array_like
                    An array with vector representation of k points.
            
            Returns
                edges : list[list[Kpoint]]
                    List of all edges that was found.
            '''
            edges = []
            bands = np.repeat(np.arange(self.min_band, self.max_band+1), len(self.neighbors[0]))
            for i_ in vectors:
                bn1 = i_//self.nks + self.min_band  # bi
                k1 = i_ % self.nks
                neighs = np.tile(self.neighbors[k1], self.nbnd)
                ks = neighs + bands*self.nks
                ks = ks[neighs != -1]
                for j_ in ks:
                    k2 = j_ % self.nks
                    bn2 = j_//self.nks + self.min_band  # bj
                    i_neig = np.where(self.neighbors[k1] == k2)[0]
                    connection = self.connections[k1, i_neig,
                                                    bn1, bn2]  # <i|j>
                    '''
                    for each first neighbor
                    Edge(i,j) = 1 iff <i, j> ~ 1
                    '''
                    if connection > tol:
                        edges.append([i_, j_])
            return edges

        # Parallelize the edges calculation
        edges = self.parallelize('Computing Edges', connection_component, range(len(self.vectors)))
        # Establish the edges on the graph from edges array
        self.GRAPH.add_edges_from(edges)

        ###########################################################################
        # Solve problems that a degenerate point may cause
        ###########################################################################
        degnerates = []
        for d1, d2 in self.degenerados:
            '''
            The degenerate points may cause problems.
            The algorithm below finds its problems and solves them.
            '''
            if not self.find_path(d1, d2):
                # Verify if exist a path that connects two forbidden points
                degnerates.append([d1, d2])
                continue
            # Obtains the neighbors from each degenerate point that cause problems
            N1 = np.array(self.get_neigs(d1))
            N2 = np.array(self.get_neigs(d2))
            if len(N1) == 0 or len(N2) == 0:
                continue
            LOG.info(f'Problem:\n\t{d1}: {N1}\n\t{d2}:{N2}')
            NKS = self.nks
            if len(N1) > 1 and len(N2) > 1:
                def n2_index(n1): return np.where(N2 % NKS == n1 % NKS)
                N = [[n1, N2[n2_index(n1)[0][0]]] for n1 in N1]
                flag = False
            else:
                if len(N1) == len(N2):
                    N = list(zip(N1, N2))
                else:
                    Ns = [N1, N2]
                    N_1 = Ns[np.argmin([len(N1), len(N2)])]
                    N_2 = Ns[np.argmax([len(N1), len(N2)])]
                    n2_index = np.where(N_2 % NKS == N_1[0] % NKS)[0][0]
                    N = [[N_1[0], N_2[n2_index]]] \
                        + [[n] for n in N_2 if n != N_2[n2_index]]
                    flag = True
            # Assign to a specific band each point and establish the corresponding edges
            n1 = np.random.choice(N[0])
            if flag:
                N1_ = [n1]
                N2_ = [N[0][np.argmax(np.abs(N[0]-n1))]]
                n2 = N2_[0]
                Ns = [N1_, N2_]
                for n in N[1:]:
                    n = n[0]
                    Ns[np.argmin(np.abs(np.array([n1, n2]) - n))].append(n)
            else:
                N1_ = [n[np.argmin(np.abs(n-n1))] for n in N]
                N2_ = [n[np.argmax(np.abs(n-n1))] for n in N]

            LOG.info(f'Solution:\n\t{d1}: {N1_}\n\t{d2}:{N2_}')
            for k in N1:
                self.GRAPH.remove_edge(k, d1)
            for k in N2:
                self.GRAPH.remove_edge(k, d2)

            for k in N1_:
                self.GRAPH.add_edge(k, d1)
            for k in N2_:
                self.GRAPH.add_edge(k, d2)
        
        self.degenerates = np.array(degnerates)

    def parallelize(self, process_name: str, f: Callable, iterator: Union[list, np.ndarray], *args, verbose: bool=True):
        process = []
        iterator = list(iterator)
        N = len(iterator)
        if verbose:
            LOG.debug(f'Starting Parallelization for {process_name} with {N} values')
        if verbose:
            LOG.percent_complete(0, N, title=process_name)

        def parallel_f(result, per, iterator, *args):
            value = f(iterator, *args)
            if value is not None:
                result += f(iterator, *args)
            per[0] += len(iterator)
            if verbose:
                LOG.percent_complete(per[0], N, title=process_name)
        
        result = Manager().list([])
        per = Manager().list([0])
        f_ = partial(parallel_f,  result, per)

        n = N//self.n_process
        for i_start in range(self.n_process):
            j_end = n*(i_start+1) if i_start < self.n_process-1\
                else n*(i_start+1) + N % self.n_process
            i_start = i_start*n
            p = Process(target=f_, args=(iterator[i_start: j_end], *args))
            p.start()
            process.append(p)

        while len(process) > 0:
            p = process.pop(0)
            p.join()

        if verbose:
            print()

        return np.array(result)

    @time_fn(prefix="\t")
    def get_components(self, tol=0.5):
        '''
        The make_connections function constructs the graph, in which
        it can detect components well constructed.
            - A component is denominated solved when it has all
              k points attributed.
            - A cluster is a significant component that can not join
              with any other cluster.
            - Otherwise, It is a sample that has to be grouped with
              some cluster.
        '''

        LOG.info('\n\nNumber of Components: ')
        LOG.info(f'{nx.number_connected_components(self.GRAPH)}')
        self.components = [COMPONENT(self.GRAPH.subgraph(c),
                                     self.kpoints_index,
                                     self.matrix)
                           for c in nx.connected_components(self.GRAPH)]
        index_sorted = np.argsort([component.N
                                   for component in self.components])[::-1]
        self.solved = []
        clusters = []
        samples = []
        for i in index_sorted:
            component = self.components[i]
            if component.N == self.nks:
                self.solved.append(component)
                continue
            component.calculate_pointsMatrix()
            component.calc_boundary()
            if len(clusters) == 0:
                clusters.append(component)
                continue
            if not np.any([cluster.validate(component)
                           for cluster in clusters]):
                clusters.append(component)
            else:
                samples.append(component)
        LOG.info(f'    Phase 1: {len(self.solved)}/{self.nbnd} Solved')
        LOG.info(f'    Initial clusters: {len(clusters)} Samples: {len(samples)}')

        count = np.array([0, len(samples)])
        while len(samples) > 0:
            evaluate_samples = np.zeros((len(samples), 2))
            for i_s, sample in enumerate(samples):
                scores = np.zeros(len(clusters))
                for j_s, cluster in enumerate(clusters):
                    if not cluster.validate(sample):
                        continue
                    if len(sample.k_edges) == 0:
                        sample.calculate_pointsMatrix()
                        sample.calc_boundary()
                    scores[j_s] = sample.get_cluster_score(cluster,
                                                           self.min_band,
                                                           self.max_band,
                                                           self.neighbors,
                                                           self.ENERGIES,
                                                           self.connections,
                                                           tol=tol)
                evaluate_samples[i_s] = np.array([np.max(scores),
                                                np.argmax(scores)])

            for cluster in clusters:
                cluster.was_modified = False
            arg_max = np.argmax(evaluate_samples[:, 0])
            sample = samples.pop(arg_max)
            score, bn = evaluate_samples[arg_max]
            bn = int(bn)
            count[0] += 1
            clusters[bn].join(sample)
            clusters[bn].was_modified = True
            LOG.percent_complete(count[0], count[1], title='Clustering Samples')
            LOG.debug(f'{count[0]}/{count[1]} Sample corrected: {score}')
            if clusters[bn].N == self.nks:
                print('Cluster Solved')
                self.solved.append(clusters.pop(bn))

        LOG.info(f'    Phase 2: {len(self.solved)}/{self.nbnd} Solved')

        if len(self.solved)/self.nbnd < 1:
            LOG.info(f'    New clusnters: {len(clusters)}')


        labels = np.empty(self.nks*self.nbnd, int)
        count = 0
        for solved in self.solved:
            labels[solved.nodes] = count
            count += 1

        for cluster in clusters:
            # cluster.save_boundary(f'cluster_{count}') # Used for analysis
            labels[cluster.nodes] = count
            count += 1
        
        self.clusters = clusters
        return labels

    @time_fn(prefix="\t")
    def obtain_output(self):
        '''
        This function prepares the final data structures
        that are essential to other programs.
        '''

        self.degenerate_final = []

        solved_bands = []
        for solved in self.solved:
            bands = solved.get_bands()
            bn = solved.bands[0] + self.min_band
            solved.bands = solved.bands[1:]
            while bn in solved_bands:
                bn = solved.bands[0] + self.min_band
                solved.bands = solved.bands[1:]
            solved_bands.append(bn)
            self.bands_final[solved.k_points, bn] = bands + self.min_band

            for k in solved.k_points:
                bn1 = solved.bands_number[k] + self.min_band
                connections = []
                for i_neig, k_neig in enumerate(self.neighbors[k]):
                    if k_neig == -1:
                        continue
                    bn2 = solved.bands_number[k_neig] + self.min_band
                    connections.append(self.connections[k, i_neig, bn1, bn2])

                self.signal_final[k, bn] = evaluate_result(connections)

        clusters_sort = np.argsort([c.N for c in self.clusters])
        for i_arg in clusters_sort[::-1]:
            cluster = self.clusters[i_arg]
            bands = cluster.get_bands()
            bn = cluster.bands[0] + self.min_band
            cluster.bands = cluster.bands[1:]
            while bn in solved_bands and len(cluster.bands) > 0:
                bn = cluster.bands[0] + self.min_band
                cluster.bands = cluster.bands[1:]

            if bn in solved_bands and len(cluster.bands) == 0:
                break

            solved_bands.append(bn)
            self.bands_final[cluster.k_points, bn] = bands + self.min_band
            for k in cluster.k_points:
                bn1 = cluster.bands_number[k] + self.min_band
                connections = []
                for i_neig, k_neig in enumerate(self.neighbors[k]):
                    if k_neig == -1:
                        continue
                    if k_neig not in cluster.k_points:
                        connections.append(0)
                        continue
                    bn2 = cluster.bands_number[k_neig] + self.min_band
                    connections.append(self.connections[k, i_neig, bn1, bn2])

                self.signal_final[k, bn] = evaluate_result(connections)

        for d1, d2 in self.degenerados:
            k1 = d1 % self.nks
            bn1 = d1 // self.nks + self.min_band
            k2 = d2 % self.nks
            bn2 = d2 // self.nks + self.min_band
            Bk1 = self.bands_final[k1] == bn1
            Bk2 = self.bands_final[k2] == bn2
            bn1 = np.argmax(Bk1) if np.sum(Bk1) != 0 else bn1
            bn2 = np.argmax(Bk2) if np.sum(Bk2) != 0 else bn2

            self.signal_final[k1, bn1] = DEGENERATE
            self.signal_final[k2, bn2] = DEGENERATE
            
            # if np.any(np.all(np.array([d1, d2]) == self.degenerates, axis=1)):
            #    self.degenerate_final.append([k1, k2, bn1, bn2])

        k_basis_rotation = []

        for bn in range(self.total_bands):
            score = 0
            for k in range(self.nks):
                if self.signal_final[k, bn] == NOT_SOLVED:
                    continue
                kneigs = self.neighbors[k]
                flag_neig = kneigs != -1
                i_neigs = np.arange(N_NEIGS)[flag_neig]
                kneigs = kneigs[flag_neig]
                flag_neig = self.signal_final[kneigs, bn] != NOT_SOLVED
                i_neigs = i_neigs[flag_neig]
                kneigs = kneigs[flag_neig]
                if len(kneigs) == 0:
                    continue
                bn_k = self.bands_final[k, bn]
                bn_neighs = self.bands_final[kneigs, bn]
                k = np.repeat(k, len(kneigs))
                bn_k = np.repeat(bn_k, len(kneigs))
                dps = self.connections[k, i_neigs, bn_k, bn_neighs]
                if np.any(np.logical_and(dps >= 0.5, dps <= 0.8)):
                    dps_deg = self.connections[k, i_neigs, bn_k]
                    k = k[0]
                    i_deg, bn_deg = np.where(np.logical_and(dps_deg >= 0.5, dps_deg <= 0.8))
                    k_deg = self.neighbors[k][i_deg+np.min(i_neigs)]
                    i_sort = np.argsort(k_deg)
                    k_deg = k_deg[i_sort]
                    bn_deg = bn_deg[i_sort]
                    k_unique, index_unique = np.unique(k_deg, return_index=True)
                    bn_unique = np.split(bn_deg, index_unique[1:])
                    len_bn = np.array([len(k_len) for k_len in bn_unique])
                    if np.any(len_bn > 1):
                        i_deg = np.where(len_bn > 1)[0]
                        k_deg = k_unique[i_deg]
                        bns_deg = [bn_unique[j_deg] for j_deg in i_deg] if len(i_deg) > 1 else [bn_unique[i_deg]]
                        k_basis_rotation.append([k, k_deg, bn, bns_deg])
                score += np.mean(dps)
            score /= self.nks
            self.final_score[bn] = score

        degenerates = []
        for i, (k, k_deg, bn, bns_deg) in enumerate(k_basis_rotation[:-1]):
            for k_, k_deg_, bn_, bns_deg_ in k_basis_rotation[i+1:]:
                if k != k_ or not np.all(k_deg == k_deg_):
                    continue
                if not np.all([np.all(np.isin(bns, bns_deg_[j])) for j, bns in enumerate(bns_deg)]):
                    continue
                
                flag = True
                for i_c, (k_c, bn_c1, bn_c2, k_deg_c) in enumerate(degenerates):
                    if k != k_c or not np.all(np.isin([bn, bn_], [bn_c1, bn_c2])) or not np.any(k_deg_c == k):
                        continue
                    flag = False
                    K = np.array([k, k])
                    K_C = np.array([k_c, k_c])
                    B_C = np.array([bn_c1, bn_c2])
                    dE = np.abs(np.sum(self.eigenvalues[K_C, self.bands_final[K_C, B_C]]*np.array(1, -1)))
                    dE_new = np.abs(np.sum(self.eigenvalues[K, self.bands_final[K, B_C]]*np.array(1, -1)))
                    
                    if dE_new < dE:
                        degenerates[i_c] = [k, bn, bn_, k_deg]

                if flag:
                    degenerates.append([k, bn, bn_, k_deg])

        for k, bn, bn_, k_deg in degenerates:
            self.degenerate_final.append([k, bn, bn_])
        
        self.degenerate_final = np.array(self.degenerate_final)

    @time_fn(prefix="\t")
    def print_report(self, signal_report):
        final_report = '\n\t====== REPORT ======\n\n'
        bands_report = []
        MAX = np.max(signal_report) + 1
        for bn in range(self.min_band, self.min_band+self.nbnd):
            band_result = signal_report[:, bn]
            report = [np.sum(band_result == s) for s in range(MAX)]
            report.append(np.round(self.final_score[bn], 4))
            bands_report.append(report)

            LOG.info(f'\n  New Band: {bn}\tnr falis: {report[0]}')
            _bands_numbers(self.nkx, self.nky, self.bands_final[:, bn])

        bands_report = np.array(bands_report)
        final_report += '\n Signaling: how many events ' + \
                        'in each band signaled.\n'
        bands_header = '\n Band | '

        header = list(range(MAX)) + [' ']
        for signal, value in enumerate(header):
            n_spaces = len(str(np.max(bands_report[:, signal])))-1
            bands_header += ' '*n_spaces+str(value) + '   '

        final_report += bands_header + '\n'
        final_report += '-'*len(bands_header)

        for bn, report in enumerate(bands_report):
            bn += self.min_band
            final_report += f'\n {bn}{" "*(4-len(str(bn)))} |' + ' '
            for signal, value in enumerate(report):
                if signal < MAX:
                    value = int(value)
                n_max = len(str(np.max(bands_report[:, signal])))
                n_spaces = n_max - len(str(value))
                final_report += ' '*n_spaces+str(value) + '   '

        LOG.info(final_report)
        self.final_report = final_report
    
    @time_fn(prefix="\t")
    def correct_signal(self):
        self.obtain_output()
        del self.GRAPH
        OTHER = 3
        MISTAKE = 1

        self.correct_signalfinal = np.copy(self.signal_final)
        self.correct_signalfinal[self.signal_final == CORRECT] = CORRECT-1

        ks_pC, bnds_pC = np.where(self.signal_final == POTENTIAL_CORRECT)
        ks_pM, bnds_pM = np.where(self.signal_final == POTENTIAL_MISTAKE)

        ks = np.concatenate((ks_pC, ks_pM))
        bnds = np.concatenate((bnds_pC, bnds_pM))

        error_directions = []
        directions = []

        for k, bn in zip(ks, bnds):
            signal, scores = evaluate_point(k, bn, self.kpoints_index,
                                            self.matrix, self.signal_final, 
                                            self.bands_final, self.eigenvalues)
            self.correct_signalfinal[k, bn] = signal
            if signal == OTHER:
                error_directions.append([k, bn])
                directions.append(scores)
            LOG.debug(f'K point: {k} Band: {bn}    New Signal: {signal} Directions: {scores}')

        k_error, bn_error = np.where(self.correct_signalfinal == MISTAKE)
        k_other, bn_other = np.where(self.correct_signalfinal == OTHER)
        other_same = self.correct_signalfinal_prev[k_other, bn_other] == OTHER
        k_ot = k_other[other_same]
        bn_ot = bn_other[other_same]
        not_same = np.logical_not(other_same)
        k_other = k_other[not_same]
        bn_other = bn_other[not_same]

        ks = np.concatenate((k_error, k_other))
        bnds = np.concatenate((bn_error, bn_other))

        bands_signaling = np.zeros((self.total_bands, *self.matrix.shape), int)
        k_index = self.kpoints_index[ks]
        ik, jk = k_index[:, 0], k_index[:, 1]
        bands_signaling[bnds, ik, jk] = 1

        mean_fitler = np.ones((3,3))
        self.GRAPH = nx.Graph()
        self.GRAPH.add_nodes_from(np.arange(len(self.vectors)))
        directions = np.array([[1, 0], [0, 1]])

        for bn, band in enumerate(bands_signaling[self.min_band: self.max_band+1]):
            bn += self.min_band
            if np.sum(band) > self.nks*0.05:
                identify_points = correlate(band, mean_fitler, output=None,
                                            mode='reflect', cval=0.0, origin=0) > 0
            else:
                identify_points = band > 0
            edges = []
            for ik, row in enumerate(identify_points):
                for jk, need_correction in enumerate(row):
                    kp = self.matrix[ik, jk]
                    if need_correction and kp not in self.degenerate_final:
                        continue
                    for direction in directions:
                        ikn, jkn = np.array([ik, jk]) + direction
                        if ikn >= self.matrix.shape[0] or jkn >= self.matrix.shape[1]:
                            continue
                        kneig = self.matrix[ikn, jkn]
                        if not identify_points[ikn, jkn]:
                            p = kp + (self.bands_final[kp, bn] - self.min_band)*self.nks
                            pn = kneig + (self.bands_final[kneig, bn] - self.min_band)*self.nks
                            edges.append([p, pn])
            edges = np.array(edges)
            self.GRAPH.add_edges_from(edges)
            self.correct_signalfinal_prev = np.copy(self.correct_signalfinal)
            self.correct_signalfinal[k_ot, bn_ot] = CORRECT-1

    @time_fn(prefix="\t")
    def solve(self, step: float=0.1, min_tol: float=0) -> None:
        '''
        This method is the main algorithm which iterates between solutions
        trying to find the best result for the material.

        Parameters
            step : float
                It is the iteration value which is used to relax the tolerance condition.
                (default 0.1)
            min_tol : float
                The minimum tolerance.
                (default 0)
        '''
        ###########################################################################
        # Initial preparation of data structures
        # The previous and best result are stored
        ###########################################################################
        TOL = 0.5   # The initial tolerance is 0.5 that is 0.5*<i|j> + 0.5*f(E)
        bands_final_flag = True
        self.bands_final_prev = np.copy(self.bands_final)
        self.best_bands_final = np.copy(self.bands_final)
        self.best_score = np.zeros(self.total_bands, dtype=float)
        self.final_score = np.zeros(self.total_bands, dtype=float)
        self.signal_final = np.zeros((self.nks, self.total_bands), dtype=int)
        self.correct_signalfinal_prev = np.full(self.signal_final.shape, -1, int)
        self.degenerate_best = None
        max_solved = 0  # The maximum number of solved bands

        ###########################################################################
        # Algorithm
        ###########################################################################
        while bands_final_flag and TOL >= min_tol:
            print()
            LOG.info(f'\n\n  Clustering samples for TOL: {TOL}')
            self.get_components(tol=TOL)                    # Obtain components from a Graph

            LOG.info('  Calculating output')        
            self.obtain_output()                            # Compute the result
            self.print_report(self.signal_final)            # Print result

            LOG.info('  Validating result')     
            self.correct_signal()                           # Evaluate the energy continuity and perform a new Graph
            self.print_report(self.correct_signalfinal)     # Print result
            
            # Verification if the result is similar to the previous one
            bands_final_flag = np.sum(np.abs(self.bands_final_prev - self.bands_final)) != 0
            self.bands_final_prev = np.copy(self.bands_final)

            # Verify and store the best result
            # To be a better result it has to be better score and all k points attributed for all the first max_solved bands
            solved = 0
            for bn, score in enumerate(self.final_score):
                best_score = self.best_score[bn]
                not_solved_prev = np.sum(self.correct_signalfinal_prev[:, bn] == NOT_SOLVED)
                not_solved = np.sum(self.correct_signalfinal[:, bn] == NOT_SOLVED)
                if score >= best_score and not_solved <= not_solved_prev:
                    solved += 1
                else:
                    break
            if solved >= max_solved:
                self.best_bands_final = np.copy(self.bands_final)
                self.best_score = np.copy(self.final_score)
                self.best_signal_final = np.copy(self.signal_final)
                self.degenerate_best = np.copy(self.degenerate_final)
                max_solved = solved
            else:
                self.bands_final = np.copy(self.best_bands_final)
                self.final_score = np.copy(self.best_score)
                self.signal_final = np.copy(self.best_signal_final)
                self.degenerate_final = np.copy(self.degenerate_best)
                self.correct_signal()
            TOL -= step
        
        # The best result is maintained
        self.bands_final = np.copy(self.best_bands_final)
        self.final_score = np.copy(self.best_score)
        self.signal_final = np.copy(self.best_signal_final)
        self.degenerate_final = np.copy(self.degenerate_best)
        
        self.print_report(self.signal_final)
        self.print_report(self.correct_signalfinal)

class COMPONENT:
    '''
    This object contains the information that constructs a component,
    and also it has functions that are necessary to establish
    relations between components.
    '''
    def __init__(self, component: nx.Graph, kpoints_index, matrix):
        self.GRAPH = component
        self.N = self.GRAPH.number_of_nodes()
        self.m_shape = matrix.shape
        self.nks = self.m_shape[0]*self.m_shape[1]
        self.kpoints_index = np.array(kpoints_index)
        self.matrix = matrix
        self.positions_matrix = None
        self.nodes = np.array(self.GRAPH.nodes)

        self.__id__ = str(self.nodes[0])
        self.was_modified = False
        self.scores = {}

    def calculate_pointsMatrix(self):
        self.positions_matrix = np.zeros(self.m_shape, int)
        index_points = self.kpoints_index[self.nodes % self.nks]
        self.k_points = self.nodes % self.nks
        self.bands_number = dict(zip(self.nodes % self.nks,
                                     self.nodes//self.nks))
        self.positions_matrix[index_points[:, 0], index_points[:, 1]] = 1

    def get_bands(self):
        self.k_points = self.nodes % self.nks
        k_bands = self.nodes//self.nks
        self.bands_number = dict(zip(self.nodes % self.nks,
                                     self.nodes//self.nks))
        bands, counts = np.unique(k_bands, return_counts=True)
        self.bands = bands[np.argsort(counts)[::-1]]
        return k_bands

    def validate(self, component):
        if self.positions_matrix is None:
            self.calculate_pointsMatrix()
        N = np.sum(self.positions_matrix ^ component.positions_matrix)
        return (component.N <= self.nks - self.N and N == self.N+component.N)

    def join(self, component):
        del component.scores
        self.was_modified = True
        G = nx.Graph(self.GRAPH)
        G.add_nodes_from(component.GRAPH)
        self.GRAPH = G
        self.N = self.GRAPH.number_of_nodes()
        self.nodes = np.array(self.GRAPH.nodes)
        self.calculate_pointsMatrix()
        self.calc_boundary()

    def calc_boundary(self):
        '''
        Only the boundary nodes are necessary. Therefore this function computes
        these essential nodes and uses them to compare components.
        '''

        if self.positions_matrix is None:
            self.calculate_pointsMatrix()
        Gx = np.array([[-1, 0, 1]]*3)
        Gy = np.array([[-1, 0, 1]]*3).T
        Ax = correlate(self.positions_matrix, Gx, output=None,
                       mode='reflect', cval=0.0, origin=0)
        Ay = correlate(self.positions_matrix, Gy, output=None,
                       mode='reflect', cval=0.0, origin=0)
        self.boundary = np.sqrt(Ax**2+Ay**2)*self.positions_matrix
        self.boundary = (self.boundary > 0)
        self.k_edges = self.matrix[self.boundary]
        if len(self.k_edges) == 0:
            self.k_edges = self.nodes % self.nks

    def get_cluster_score(self, cluster, min_band, max_band,
                          neighbors, energies, connections, tol = 0.5):
        '''
        This function returns the similarity between components taking
        into account the dot product of all essential points and their
        energy value.
        INPUT
        cluster: It is a component with which the similarity is calculated.
        min_band: It is an integer that gives the minimum band used for clustering.
        max_band: It is an integer that gives the maximum band used for clustering.
        neighbors: It is an array that identifies the neighbors of each k point.
        energies: It is an array of the energy values inside a matrix.
        connections: It is an array with the dot product between k points
                     and his neighbors.
        OUTPUT
        score: It is a float that represents the similarity between components.
        '''
        def difference_energy(bn1, bn2, iK1, iK2, Ei = None):
            ik1, jk1 = iK1
            ik_n, jk_n = iK2
            Ei = energies[bn1, ik1, jk1] if Ei is None else Ei
            bands = np.arange(min_band, max_band+1)
            min_energy = np.min([np.abs(Ei-energies[bn, ik_n, jk_n])
                                    for bn in bands])
            delta_energy = np.abs(Ei-energies[bn2, ik_n, jk_n])
            return min_energy/delta_energy if delta_energy else 1
        
        def fit_energy(bn1, bn2, iK1, iK2):
            N = 4 # Number of points taking in account
            ik1, jk1 = iK1
            ik_n, jk_n = iK2
            I = np.full(N+1,ik1)
            J = np.full(N+1,jk1)
            flag = ik1 == ik_n
            i = I if flag else I + np.arange(0,N+1)*np.sign(ik1-ik_n)
            j = J if not flag else J + np.arange(0,N+1)*np.sign(jk1-jk_n)
            
            if not flag:
                i = i[i >= 0]
                i = i[i < self.m_shape[0]]
                j = np.full(len(i), jk1)
            else:
                j = j[j >= 0]
                j = j[j < self.m_shape[1]]
                i = np.full(len(j), ik1)


            ks = self.matrix[i, j]
            f = lambda e: e in self.k_points
            exist_ks = list(map(f, ks))
            ks = ks[exist_ks]
            if len(ks) <= 3:
                return difference_energy(bn1, bn2, iK1, iK2)
            aux_bands = np.array([self.bands_number[kp] for kp in ks])
            bands = aux_bands + min_band
            i = i[exist_ks]
            j = j[exist_ks]
            Es = energies[bands, i, j]
            X = i if jk1 == jk_n else j
            new_x = ik_n if jk1 == jk_n else jk_n

            pol = lambda x, a, b, c: a*x**2 + b*x + c
            popt, pcov = curve_fit(pol, X, Es)
            Enew = pol(new_x, *popt)
            Ei = energies[bn1, ik1, jk1]
            # LOG.debug(f'Actual Energy: {Ei} Energy founded: {Enew} for {bn1} with {len(i)} points.')
            return difference_energy(bn1, bn2, iK1, iK2, Ei = Enew)


        if cluster.was_modified:
            return self.scores[cluster.__id__]
        
        cluster.was_modified = False
        score = 0
        for k in self.k_edges:
            bn1 = self.bands_number[k] + min_band
            ik1, jk1 = self.kpoints_index[k]
            for i_neig, k_n in enumerate(neighbors[k]):
                if k_n == -1 or k_n not in cluster.k_edges:
                    continue
                ik_n, jk_n = self.kpoints_index[k_n]
                bn2 = cluster.bands_number[k_n]+min_band
                connection = connections[k, i_neig, bn1, bn2]
                energy_val = fit_energy(bn1, bn2, (ik1, jk1), (ik_n, jk_n))
                score += tol*connection + (1-tol)*energy_val
        score /= len(self.k_edges)*4
        self.scores[cluster.__id__] = score
        return score

    def save_boundary(self, filename):
        if not os.path.exists("boundaries/"):
            os.mkdir('boundaries/')
        with open('boundaries/'+filename+'.npy', 'wb') as f:
            np.save(f, self.boundary)
            np.save(f, self.positions_matrix)
