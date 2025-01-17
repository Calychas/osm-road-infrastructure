import sys
import click
import geopandas as gpd
from pathlib import Path
from typing import List, Optional

PROJECT_DIR = Path().parent.parent.resolve()
sys.path.append(str(PROJECT_DIR))
sys.path.append(str(PROJECT_DIR.joinpath("src").resolve()))


from src.tools.osmnx_utils import generate_data_for_place, get_place_dir_name
from src.tools.h3_utils import generate_hexagons_for_place, assign_hexagons_to_edges, get_resolution_buffered_suffix, get_edges_with_features_filename
from src.tools.feature_extraction import generate_features_for_edges




@click.group()
def main():
    pass


@main.command()
@click.argument('place_name')
@click.argument('out_dir', type=click.Path(file_okay=False))
@click.argument('network_type', default="drive")
@click.option('--h3', '-h', "h3_resolutions", multiple=True, default=[6, 7, 8, 9, 10])
@click.option('--region', '-r', "regions", multiple=True, default=[None])
def download(place_name: str, out_dir: str, network_type: str, h3_resolutions: List[int], regions: Optional[List[str]]):
    regions = list(regions)
    regions = regions[0] if regions[0] is None else regions
    generate_data_for_place(place_name, out_dir, h3_resolutions=h3_resolutions, network_type=network_type, regions=regions)


@main.command()
@click.argument('place_dir', type=click.Path(file_okay=False))
@click.argument('resolution', default=9)
@click.argument('buffer', default=False)
@click.argument('network_type', default="drive")
def h3(place_dir: str, resolution: int, buffer: bool, network_type: str):
    gpkg_path = Path(place_dir) / f"graph_{network_type}.gpkg"
    place: gpd.GeoDataFrame = gpd.read_file(gpkg_path, layer="place")  # type: ignore
    generate_hexagons_for_place(place, resolution, place_dir, network_type, buffer)


@main.command()
@click.argument('place_dir', type=click.Path(file_okay=False))
@click.argument('network_type', default="drive")
@click.argument('resolution', default=9)
@click.argument('buffered', default=False)
@click.argument('intersection_based', default=True)
def features(place_dir: str, network_type: str, resolution: int, buffered: bool, intersection_based: bool, featureset: dict):
    gpkg_path = Path(place_dir) / f"graph_{network_type}.gpkg"
    
    hexagons: gpd.GeoDataFrame = gpd.read_file(gpkg_path, layer=f"hex_{get_resolution_buffered_suffix(resolution, buffered)}")  # type: ignore

    nodes = gpd.read_file(gpkg_path, layer="nodes") if intersection_based else None
    edges = gpd.read_file(gpkg_path, layer="edges")
    edges_with_hexagons = assign_hexagons_to_edges(edges, hexagons, nodes)  # type: ignore

    edges_with_features = generate_features_for_edges(edges_with_hexagons, featureset)

    edges_with_features.to_feather(Path(place_dir) / get_edges_with_features_filename(network_type, resolution, buffered, intersection_based)) # MASSIVE SPEEDUP IN WRITING AND ALSO SMALLER FILE SIZE


if __name__ == "__main__":
    main()
