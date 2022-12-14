import os
from typing import Optional, List, Dict, Tuple
import click
import panqec
from tqdm import tqdm
import shutil
import numpy as np
import json
from json.decoder import JSONDecodeError
import multiprocessing
import datetime
import time
import psutil
import gzip
from .simulation import (
    run_file, merge_results_dicts, merge_lists_of_results_dicts
)
from .config import CODES, ERROR_MODELS, DECODERS, PANQEC_DIR, BASE_DIR
from .slurm import (
    generate_sbatch, get_status, generate_sbatch_ad, count_input_runs,
    clear_out_folder, clear_sbatch_folder
)
from .utils import get_direction_from_bias_ratio
from panqec.gui import GUI
from glob import glob
from .usage import summarize_usage
from .analysis import Analysis


@click.group(invoke_without_command=True)
@click.version_option(version=panqec.__version__, prog_name='panqec')
@click.pass_context
def cli(ctx):
    """
    panqec - biased noise in 3D simulations.

    See panqec COMMAND --help for command-specific help.
    """
    if not ctx.invoked_subcommand:
        print(ctx.get_help())


@click.command()
@click.option('-p', '--port', 'port')
def start_gui(port: Optional[int]):
    gui = GUI()
    gui.run(port=port)


@click.command()
@click.pass_context
@click.option('-f', '--file', 'file_')
@click.option('-o', '--output_dir', type=click.STRING)
@click.option('-t', '--trials', default=100, type=click.INT, show_default=True)
@click.option('-s', '--start', default=None, type=click.INT, show_default=True)
@click.option(
    '-n', '--n_runs', default=None, type=click.INT, show_default=True
)
def run(
    ctx,
    file_: Optional[str],
    output_dir: Optional[str],
    trials: int,
    start: Optional[int] = None,
    n_runs: Optional[int] = None
):
    """Run a single job or run many jobs from input file."""
    if file_ is not None:
        run_file(
            os.path.abspath(file_), trials,
            start=start, n_runs=n_runs, progress=tqdm,
            output_dir=output_dir
        )
    else:
        print(ctx.get_help())


@click.command()
@click.option('-d', '--data_dir')
@click.option(
    '-t', '--trials', default=1000, type=click.INT, show_default=True
)
@click.option(
    '-n', '--n_nodes', default=1, type=click.INT, show_default=True
)
@click.option(
    '-j', '--job_idx', default=1, type=click.INT, show_default=True
)
@click.option(
    '-c', '--n_cores', default=None, type=click.INT, show_default=True
)
@click.option(
    '--delete-existing', is_flag=True, default=False, show_default=True,
    help="Delete existing results folder in the data directory"
)
def run_parallel(
    data_dir: str,
    trials: int,
    n_nodes: int,
    job_idx: int,
    n_cores: Optional[int],
    delete_existing: bool
):
    """Run panqec jobs in parallel"""

    input_dir = os.path.join(data_dir, "inputs")
    result_dir = os.path.join(data_dir, "results")

    i_node = job_idx - 1

    assert 1 <= job_idx <= n_nodes, \
        f"job_id={job_idx} is invalid. It must be between 1 and {n_nodes}"

    n_cpu = multiprocessing.cpu_count()

    if not n_cores:
        n_cores = n_cpu

    assert n_cores <= n_cpu, \
        f"The number of cores requested ({n_cores}) is higher than" \
        f"the total number of cores ({n_cpu})"

    print(f"Running job {job_idx}/{n_nodes} on {n_cores} cores")

    n_tasks = n_nodes * n_cores

    print(f"Total number of tasks: {n_tasks}\n")

    list_inputs = glob(f"{input_dir}/*.json")

    print("List inputs", list_inputs)

    n_inputs = len(list_inputs)

    if n_inputs == 0:
        raise ValueError(f"No input files in {input_dir}")

    procs = []
    for i_core in range(n_cores):
        i_task = n_cores * i_node + i_core

        n_tasks_per_input = n_tasks // n_inputs

        i_input = i_task // n_tasks_per_input
        if i_input >= n_inputs:
            i_input = n_inputs - 1

        if i_input == n_inputs - 1:
            n_tasks_per_input = n_tasks_per_input + n_tasks % n_inputs

        i_task_in_input = i_task % n_tasks_per_input

        if i_input == n_inputs - 1:
            i_task_in_input = i_task - n_tasks // n_inputs * (n_inputs - 1)

        n_runs = trials // n_tasks_per_input

        if i_task_in_input == n_tasks_per_input - 1:
            n_runs += trials % n_runs

        filename = list_inputs[i_input]
        input_name = os.path.basename(filename)

        # Split the results over files results_1.json, results_2.json, etc.
        max_n_digits = len(str(n_tasks))
        result_dir = os.path.abspath(os.path.join(
            data_dir,
            f"results_{str(i_task+1).zfill(max_n_digits)}"
        ))

        if delete_existing and os.path.exists(result_dir):
            shutil.rmtree(result_dir)

        os.makedirs(result_dir, exist_ok=True)

        print(f"{input_name}\t{result_dir}\t{n_runs}")

        input_file = os.path.abspath(os.path.join(input_dir, input_name))

        proc = multiprocessing.Process(
            target=run_file,
            args=(input_file, n_runs),
            kwargs={'output_dir': result_dir, 'progress': tqdm}
        )
        procs.append(proc)
        proc.start()

    # complete the processes
    for proc in procs:
        proc.join()


@click.command()
@click.argument('model_type', required=False, type=click.Choice(
    ['codes', 'error_models', 'decoders'],
    case_sensitive=False
))
def ls(model_type=None):
    """List available codes, error models and decoders."""
    if model_type is None or model_type == 'codes':
        print('Codes:')
        print('\n'.join([
            '    ' + name for name in sorted(CODES.keys())
        ]))
    if model_type is None or model_type == 'error_models':
        print('Error Models (Noise):')
        print('\n'.join([
            '    ' + name for name in sorted(ERROR_MODELS.keys())
        ]))
    if model_type is None or model_type == 'decoders':
        print('Decoders:')
        print('\n'.join([
            '    ' + name for name in sorted(DECODERS.keys())
        ]))


def read_bias_ratios(eta_string: str) -> list:
    """Read bias ratios from comma separated string."""
    bias_ratios = []
    for s in eta_string.split(','):
        s = s.strip()
        if s == 'inf':
            bias_ratios.append(np.inf)
        elif float(s) % 1 == 0:
            bias_ratios.append(int(s))
        else:
            bias_ratios.append(float(s))
    return bias_ratios


def read_range_input(specification: str) -> List[float]:
    """Read range input string and return list."""
    values: List[float] = []
    if ':' in specification:
        parts = specification.split(':')
        min_value = float(parts[0])
        max_value = float(parts[1])
        step = 0.005
        if len(parts) == 3:
            step = float(parts[2])
        values = np.arange(min_value, max_value + step, step).tolist()
    elif ',' in specification:
        values = [float(s) for s in specification.split(',')]
    else:
        values = [float(specification)]
    return values


@click.command()
@click.option(
    '-o', '--overrides', type=click.Path(exists=True),
    default=None,
    help='Overrides specification .json file.'
)
@click.option(
    '-p', '--plot_dir', type=click.Path(),
    default=os.path.join(PANQEC_DIR, 'plots'),
    help='Directory to save plots in.'
)
@click.argument(
    'paths', nargs=-1, type=click.Path(exists=True),
)
def analyze(paths, overrides, plot_dir):
    """Analyze the data at given paths."""

    # Use headless plotting and ignore warnings from matplotlib.
    import matplotlib
    matplotlib.use('Agg')
    import warnings
    warnings.filterwarnings('ignore')

    analysis = Analysis(list(paths), overrides=overrides, verbose=True)
    analysis.analyze(progress=tqdm)
    analysis.make_plots(plot_dir)
    analysis.save(os.path.join(plot_dir, 'analysis.json.gz'))


@click.command()
@click.option('log_file', type=str, required=True)
@click.option(
    '-i', '--interval', default=10, type=click.INT,
    show_default=True, required=True
)
def monitor_usage(log_file: str, interval: float = 10):
    """Continously monitor CPU usage by logging to file at intervals.

    Parameters
    ----------
    log_file : str
        Path to log file where messages are saved.
    interval : int
        Interval at which to check usage, in seconds.
    """
    ppid = os.getppid()
    if not os.path.isfile(log_file):
        with open(log_file, 'w') as f:
            f.write(f'Log file for {ppid}\n')
    while True:
        cpu_usage = psutil.cpu_percent(percpu=True)
        mean_cpu_usage = np.mean(cpu_usage)
        n_cores = len(cpu_usage)
        time_now = datetime.datetime.now()
        mem = psutil.virtual_memory()
        ram_usage = mem.percent
        ram_total = mem.total/2**30
        message = (
            f'{time_now} CPU usage {mean_cpu_usage:.2f}% '
            f'({n_cores} cores) '
            f'RAM {ram_usage:.2f}% ({ram_total:.2f} GiB tot)'
        )
        with open(log_file, 'a') as f:
            f.write(message + '\n')
        time.sleep(interval)


@click.command()
@click.option(
    '-d', '--data_dir', required=True, type=str,
    help='Directory to save input .json files, as'
    '`[data_dir]/inputs/input_bias_[eta].json`'
)
@click.option(
    '-r', '--ratio', default='equal', type=click.Choice(['equal', 'coprime']),
    show_default=True, help='Lattice aspect ratio spec'
)
@click.option(
    '--decoder_class', default='BeliefPropagationOSDDecoder',
    show_default=True,
    type=click.Choice(list(DECODERS.keys())),
    help='Decoder class name'
)
@click.option(
    '-s', '--sizes', default='5,9,7,13', type=str,
    show_default=True,
    help='List of sizes'
)
@click.option(
    '--bias', default='Z', type=click.Choice(['X', 'Y', 'Z']),
    show_default=True,
    help='Pauli bias'
)
@click.option(
    '--eta', default='0.5,1,3,10,30,100,inf', type=str,
    show_default=True,
    help='Bias ratio'
)
@click.option(
    '--prob', default='0:0.6:0.005', type=str,
    show_default=True,
    help='min:max:step or single value or list of values'
)
@click.option(
    '--code_class', default=None, type=str,
    show_default=True,
    help='Code class name, e.g. Toric3DCode'
)
@click.option(
    '--noise_class', default='PauliErrorModel', type=str,
    show_default=True,
    help='Error model class name, e.g. PauliErrorModel'
)
@click.option(
    '--deformation_name', default=None, type=str,
    show_default=True,
    help='Name of the Clifford deformation to use in our noise, e.g. XZZX'
)
@click.option(
    '-m', '--method', default='direct',
    show_default=True,
    type=click.Choice(['direct', 'splitting']),
    help='Simulation method, between "direct" (simple Monte-Carlo simulation)'
         'and "splitting" (Metropolis-Hastings for low error rates)'
)
@click.option(
    '-l', '--label', default=None,
    show_default=True,
    type=str,
    help='Label for the inputs'
)
def generate_input(
    data_dir, ratio, sizes, decoder_class, bias, eta, prob,
    code_class, noise_class, deformation_name, method, label
):
    """Generate the json files of every experiment.

    \b
    Example:
    panqec generate-input -i data/toric-3d-code/ \\
            --code_class Toric3DCode \\
            --noise_class PauliErrorModel
            -r equal \\
            -s 2,4,6,8 --decoder BeliefPropagationOSDDecoder \\
            --bias Z --eta '10,100,1000,inf' \\
            --prob 0:0.5:0.005
    """
    input_dir = os.path.join(data_dir, 'inputs')
    os.makedirs(input_dir, exist_ok=True)

    error_rates = read_range_input(prob)
    bias_ratios = read_bias_ratios(eta)

    for eta in bias_ratios:
        direction = get_direction_from_bias_ratio(bias, eta)

        L_list = [int(s) for s in sizes.split(',')]
        if ratio == 'coprime':
            code_parameters = [
                {"L_x": L, "L_y": L + 1, "L_z": L}
                for L in L_list
            ]
        else:
            code_parameters = [
                {"L_x": L, "L_y": L, "L_z": L}
                for L in L_list
            ]

        code_dict = {
            "name": code_class,
            "parameters": code_parameters
        }

        noise_parameters = direction
        if deformation_name is not None:
            noise_parameters['deformation_name'] = deformation_name

        error_model_dict = {
            "name": noise_class,
            "parameters": noise_parameters
        }

        if decoder_class == "BeliefPropagationOSDDecoder":
            decoder_parameters = {'max_bp_iter': 1000,
                                  'osd_order': 100}
        else:
            decoder_parameters = {}

        method_parameters = {}
        if method == 'splitting':
            method_parameters['n_init_runs'] = 20000

        method_dict = {
            'name': method,
            'parameters': method_parameters
        }

        decoder_dict = {"name": decoder_class,
                        "parameters": decoder_parameters}

        if label is None:
            label = 'input'

        label = label + f'_bias_{eta}'

        ranges_dict = {"label": label,
                       "method": method_dict,
                       "code": code_dict,
                       "error_model": error_model_dict,
                       "decoder": decoder_dict,
                       "error_rate": error_rates}

        json_dict = {"comments": "",
                     "ranges": ranges_dict}

        filename = os.path.join(input_dir, f'{label}.json')

        with open(filename, 'w') as json_file:
            json.dump(json_dict, json_file, indent=4)


@click.group(invoke_without_command=True)
@click.pass_context
def slurm(ctx):
    """Routines for generating and running slurm scripts."""
    if not ctx.invoked_subcommand:
        print(ctx.get_help())


@click.command()
@click.option('-o', '--outdir', required=True, type=str, nargs=1)
@click.argument('dirs', type=click.Path(exists=True), nargs=-1)
def merge_dirs(outdir, dirs):
    """Merge result directories that had been split into outdir."""
    os.makedirs(outdir, exist_ok=True)

    if len(dirs) == 0:
        results_dirs = glob(os.path.join(os.path.dirname(outdir), 'results_*'))
        results_dirs = [path for path in results_dirs if os.path.isdir(path)]
        print(results_dirs)
    else:
        results_dirs = list(dirs)

    print(f'Merging {len(results_dirs)} dirs into {outdir}')
    file_lists: Dict[Tuple[str, str], List[str]] = dict()
    for res_dir in results_dirs:
        print(res_dir)
        file_list = glob(os.path.join(res_dir, '*.json'))
        file_list += glob(os.path.join(res_dir, '*.json.gz'))

        print(file_list)
        for file_path in file_list:
            base_name = os.path.basename(file_path)
            key = (res_dir, base_name)
            if key not in file_lists:
                file_lists[key] = []
            file_lists[key].append(file_path)
    print(len(file_lists))

    iterator = tqdm(file_lists.items(), total=len(file_lists))
    for (res_dir, base_name), file_list in iterator:
        os.makedirs(os.path.join(outdir, res_dir), exist_ok=True)
        combined_file = os.path.join(outdir, res_dir, base_name)

        results_dicts = []
        for file_path in file_list:
            try:
                if os.path.splitext(file_path)[-1] == '.json':
                    with open(file_path) as f:
                        results_dicts.append(json.load(f))
                else:
                    with gzip.open(file_path, 'rb') as gz:
                        results_dicts.append(
                            json.loads(gz.read().decode('utf-8'))
                        )
            except JSONDecodeError:
                print(f'Error reading {file_path}, skipping')

        # If any combined files, flatten the lists of dicts into dicts.
        if any(isinstance(element, list) for element in results_dicts):
            print('test 1')
            combined_results = merge_lists_of_results_dicts(results_dicts)

        # Otherwise deal with it the old way.
        else:
            print('test 2')
            combined_results = merge_results_dicts(results_dicts)

        if os.path.splitext(file_path)[-1] == '.json':
            with open(combined_file, 'w') as f:
                json.dump(combined_results, f)
        else:
            with gzip.open(combined_file, 'wb') as gz:
                gz.write(json.dumps(combined_results).encode('utf-8'))


@click.command()
@click.argument('sbatch_file', required=True)
@click.option('-d', '--data_dir', type=click.Path(exists=True), required=True)
@click.option('-n', '--n_array', default=6, type=click.INT, show_default=True)
@click.option('-q', '--queue', default='defq', type=str, show_default=True)
@click.option(
    '-w', '--wall_time', default='0-20:00', type=str, show_default=True
)
@click.option(
    '-t', '--trials', default='0-20:00', type=str, show_default=True
)
@click.option(
    '-s', '--split', default=1, type=click.INT, show_default=True
)
def pi_sbatch(sbatch_file, data_dir, n_array, queue, wall_time, trials, split):
    """Generate PI-style sbatch file with parallel and array job."""
    template_file = os.path.join(
        os.path.dirname(BASE_DIR), 'scripts', 'pi_template.sh'
    )
    with open(template_file) as f:
        text = f.read()

    inputs_dir = os.path.join(data_dir, 'inputs')
    assert os.path.isdir(inputs_dir), (
        f'{inputs_dir} missing, please create it and generate inputs'
    )
    name = os.path.basename(data_dir)
    replace_map = {
        '${TRIALS}': trials,
        '${DATADIR}': data_dir,
        '${TIME}': wall_time,
        '${NAME}': name,
        '${NARRAY}': str(n_array),
        '${QUEUE}': queue,
        '${SPLIT}': str(split),
    }
    for template_string, value in replace_map.items():
        text = text.replace(template_string, value)

    with open(sbatch_file, 'w') as f:
        f.write(text)
    print(f'Wrote to {sbatch_file}')


@click.command()
@click.argument('sbatch_file', required=True)
@click.option('-d', '--data_dir', type=click.Path(exists=True), required=True)
@click.option('-n', '--n_array', default=6, type=click.INT, show_default=True)
@click.option(
    '-a', '--account', default='def-raymond', type=str, show_default=True
)
@click.option(
    '-e', '--email', default='mvasmer@pitp.ca', type=str, show_default=True
)
@click.option(
    '-w', '--wall_time', default='04:00:00', type=str, show_default=True
)
@click.option(
    '-m', '--memory', default='16GB', type=str, show_default=True
)
@click.option(
    '-t', '--trials', default=1000, type=click.INT, show_default=True
)
@click.option(
    '-s', '--split', default='auto', type=str, show_default=True
)
def cc_sbatch(
    sbatch_file, data_dir, n_array, account, email, wall_time, memory, trials,
    split
):
    """Generate Compute Canada-style sbatch file with parallel array jobs."""
    template_file = os.path.join(
        os.path.dirname(BASE_DIR), 'scripts', 'cc_template.sh'
    )
    with open(template_file) as f:
        text = f.read()

    inputs_dir = os.path.join(data_dir, 'inputs')
    assert os.path.isdir(inputs_dir), (
        f'{inputs_dir} missing, please create it and generate inputs'
    )
    name = os.path.basename(data_dir)
    replace_map = {
        '${ACCOUNT}': account,
        '${EMAIL}': email,
        '${TIME}': wall_time,
        '${MEMORY}': memory,
        '${NAME}': name,
        '${NARRAY}': str(n_array),
        '${DATADIR}': os.path.abspath(data_dir),
        '${TRIALS}': str(trials),
        '${SPLIT}': str(split),
    }
    for template_string, value in replace_map.items():
        text = text.replace(template_string, value)

    with open(sbatch_file, 'w') as f:
        f.write(text)
    print(f'Wrote to {sbatch_file}')


@click.command()
@click.argument('sbatch_file', required=True)
@click.option('-d', '--data_dir', type=click.Path(exists=True), required=True)
@click.option('-n', '--n_array', default=6, type=click.INT, show_default=True)
@click.option(
    '-w', '--wall_time', default='0-23:00', type=str, show_default=True
)
@click.option(
    '-m', '--memory', default='32GB', type=str, show_default=True
)
@click.option(
    '-t', '--trials', default=1000, type=click.INT, show_default=True
)
@click.option(
    '-s', '--split', default='auto', type=str, show_default=True
)
@click.option('-p', '--partition', default='pml', type=str, show_default=True)
@click.option(
    '--max_sim_array', default=None, type=int, show_default=True,
    help='Max number of simultaneous array jobs'
)
def ad_sbatch(
    sbatch_file, data_dir, n_array, wall_time, memory, trials, split,
    partition, max_sim_array
):
    """Generate AD-style sbatch file with parallel array jobs."""
    template_file = os.path.join(
        os.path.dirname(BASE_DIR), 'scripts', 'ad_template.sh'
    )
    with open(template_file) as f:
        text = f.read()

    inputs_dir = os.path.join(data_dir, 'inputs')
    assert os.path.isdir(inputs_dir), (
        f'{inputs_dir} missing, please create it and generate inputs'
    )
    name = os.path.basename(data_dir)
    narray_str = str(n_array)
    if max_sim_array is not None:
        narray_str += '%' + str(max_sim_array)
    replace_map = {
        '${TIME}': wall_time,
        '${MEMORY}': memory,
        '${NAME}': name,
        '${NARRAY}': narray_str,
        '${DATADIR}': os.path.abspath(data_dir),
        '${TRIALS}': str(trials),
        '${SPLIT}': str(split),
        '${QUEUE}': partition,
    }
    for template_string, value in replace_map.items():
        text = text.replace(template_string, value)

    with open(sbatch_file, 'w') as f:
        f.write(text)
    print(f'Wrote to {sbatch_file}')


@click.command()
@click.argument('qsub_file', required=True)
@click.option('-d', '--data_dir', type=click.Path(exists=True), required=True)
@click.option('-n', '--n_array', default=6, type=click.INT, show_default=True)
@click.option(
    '-w', '--wall_time', default='0-23:00', type=str, show_default=True
)
@click.option(
    '-m', '--memory', default='32GB', type=str, show_default=True
)
@click.option(
    '-t', '--trials', default=1000, type=click.INT, show_default=True
)
@click.option(
    '-c', '--cores', default=1, type=click.INT, show_default=True
)
@click.option('-p', '--partition', default='pml', type=str, show_default=True)
def generate_qsub(
    qsub_file, data_dir, n_array, wall_time, memory, trials, cores, partition
):
    """Generate qsub (PBS) file with parallel array jobs."""
    template_file = os.path.join(
        os.path.dirname(BASE_DIR), 'scripts', 'qsub_template.sh'
    )
    with open(template_file) as f:
        text = f.read()

    inputs_dir = os.path.join(data_dir, 'inputs')
    assert os.path.isdir(inputs_dir), (
        f'{inputs_dir} missing, please create it and generate inputs'
    )
    name = os.path.basename(data_dir)
    replace_map = {
        '${TIME}': wall_time,
        '${MEMORY}': memory,
        '${NAME}': name,
        '${NARRAY}': str(n_array),
        '${DATADIR}': os.path.abspath(data_dir),
        '${TRIALS}': str(trials),
        '${CORES}': str(cores),
        '${QUEUE}': partition,
    }
    for template_string, value in replace_map.items():
        text = text.replace(template_string, value)

    with open(qsub_file, 'w') as f:
        f.write(text)
    print(f'Wrote to {qsub_file}')


@click.command()
@click.argument('sbatch_file', required=True)
@click.option('-d', '--data_dir', type=click.Path(exists=True), required=True)
@click.option('-n', '--n_array', default=6, type=click.INT, show_default=True)
@click.option(
    '-w', '--wall_time', default='0-23:00', type=str, show_default=True
)
@click.option(
    '-m', '--memory', default='32GB', type=str, show_default=True
)
@click.option(
    '-t', '--trials', default=1000, type=click.INT, show_default=True
)
@click.option(
    '-s', '--split', default='auto', type=str,
    show_default=True
)
@click.option(
    '-p', '--partition', default='dpart', type=str, show_default=True
)
@click.option(
    '-q', '--qos', default='dpart', type=str, show_default=True
)
def umiacs_sbatch(
    sbatch_file, data_dir, n_array, wall_time, memory, trials, split,
    partition, qos
):
    """Generate UMIACS-style sbatch file with parallel array jobs."""
    template_file = os.path.join(
        os.path.dirname(BASE_DIR), 'scripts', 'umiacs_template.sh'
    )
    with open(template_file) as f:
        text = f.read()

    inputs_dir = os.path.join(data_dir, 'inputs')
    assert os.path.isdir(inputs_dir), (
        f'{inputs_dir} missing, please create it and generate inputs'
    )
    name = os.path.basename(data_dir)
    replace_map = {
        '${TIME}': wall_time,
        '${MEMORY}': memory,
        '${NAME}': name,
        '${NARRAY}': str(n_array),
        '${DATADIR}': os.path.abspath(data_dir),
        '${TRIALS}': str(trials),
        '${SPLIT}': str(split),
        '${QOS}': qos,
        '${QUEUE}': partition,
    }
    for template_string, value in replace_map.items():
        text = text.replace(template_string, value)

    with open(sbatch_file, 'w') as f:
        f.write(text)
    print(f'Wrote to {sbatch_file}')


@click.command()
@click.option('--n_trials', default=1000, type=click.INT, show_default=True)
@click.option('--partition', default='defq', show_default=True)
@click.option('--time', default='10:00:00', show_default=True)
@click.option('--cores', default=1, type=click.INT, show_default=True)
def gen(n_trials, partition, time, cores):
    """Generate sbatch files."""
    generate_sbatch(n_trials, partition, time, cores)


@click.command()
@click.argument('name', required=True)
@click.option('--n_trials', default=1000, type=click.INT, show_default=True)
@click.option('--nodes', default=1, type=click.INT, show_default=True)
@click.option('--ntasks', default=1, type=click.INT, show_default=True)
@click.option('--cpus_per_task', default=40, type=click.INT, show_default=True)
@click.option('--mem', default=10000, type=click.INT, show_default=True)
@click.option('--time', default='10:00:00', show_default=True)
@click.option('--split', default=1, type=click.INT, show_default=True)
@click.option('--partition', default='pml', show_default=True)
@click.option(
    '--cluster', default='ad', show_default=True,
    type=click.Choice(['ad', 'symmetry'])
)
def gen_ad(
    name, n_trials, nodes, ntasks, cpus_per_task, mem, time, split, partition,
    cluster
):
    """Generate sbatch files for AD cluster."""
    generate_sbatch_ad(
        name, n_trials, nodes, ntasks, cpus_per_task, mem, time, split,
        partition, cluster
    )


@click.command()
@click.argument('folder', required=True, type=click.Choice(
    ['all', 'out', 'sbatch'],
    case_sensitive=False
))
def clear(folder):
    """Clear generated files."""
    if folder == 'out' or folder == 'all':
        clear_out_folder()
    if folder == 'sbatch' or folder == 'all':
        clear_sbatch_folder()


@click.command()
@click.argument('name', required=True)
def count(name):
    """Count number of input parameters contained."""
    n_runs = count_input_runs(name)
    print(n_runs)


@click.command()
def status():
    """Show the status of running jobs."""
    get_status()


@click.command
@click.argument(
    'data_dirs', default=None, type=click.Path(exists=True), nargs=-1
)
def check_usage(data_dirs=None):
    """Check usage of resources."""
    log_dirs = []
    if data_dirs:
        for data_dir in data_dirs:
            log_dir = os.path.join(data_dir, 'logs')
            if not os.path.isdir(log_dir):
                print(f'{log_dir} not a directory')
            else:
                log_dirs.append(log_dir)
    else:
        log_dirs = glob(os.path.join(PANQEC_DIR, 'paper', '*', 'logs'))
    summarize_usage(log_dirs)


slurm.add_command(gen)
slurm.add_command(gen_ad)
slurm.add_command(status)
slurm.add_command(count)
slurm.add_command(clear)
cli.add_command(start_gui)
cli.add_command(run)
cli.add_command(run_parallel)
cli.add_command(ls)
cli.add_command(slurm)
cli.add_command(generate_input)
cli.add_command(monitor_usage)
cli.add_command(pi_sbatch)
cli.add_command(cc_sbatch)
cli.add_command(merge_dirs)
cli.add_command(ad_sbatch)
cli.add_command(generate_qsub)
cli.add_command(umiacs_sbatch)
cli.add_command(check_usage)
cli.add_command(analyze)
