# Copyright 2022 the Regents of the University of California, Nerfstudio Team and contributors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Processes an image sequence to a nerfstudio compatible dataset."""

from dataclasses import dataclass
from typing import Optional

from nerfstudio.process_data import equirect_utils, process_data_utils
from nerfstudio.process_data.colmap_converter_to_nerfstudio_dataset import ColmapConverterToNerfstudioDataset

from nerfstudio.utils.rich_utils import CONSOLE


@dataclass
class ImagesToNerfstudioDataset(ColmapConverterToNerfstudioDataset):
    """Process images into a nerfstudio dataset.

    1. Scales images to a specified size.
    2. Calculates the camera poses for each image using `COLMAP <https://colmap.github.io/>`_.
    """

    percent_radius_crop: float = 1.0	
    """Create circle crop mask. The radius is the percent of the image diagonal."""

    def main(self) -> None:
        """Process images into a nerfstudio dataset."""

        require_cameras_exist = False
        if self.colmap_model_path != ColmapConverterToNerfstudioDataset.default_colmap_path():
            if not self.skip_colmap:
                raise RuntimeError("The --colmap-model-path can only be used when --skip-colmap is not set.")
            if not (self.output_dir / self.colmap_model_path).exists():
                raise RuntimeError(f"The colmap-model-path {self.output_dir / self.colmap_model_path} does not exist.")
            require_cameras_exist = True

        image_rename_map: Optional[dict[str, str]] = None

        # Generate planar projections if equirectangular
        if self.camera_type == "equirectangular":
            if self.eval_data is not None:	
                raise ValueError("Cannot use eval_data with camera_type equirectangular.")
            pers_size = pers_size_hdr = equirect_utils.compute_resolution_from_equirect(self.data, self.images_per_equirect)
            if self.hdr_dir is not None:
                pers_size_hdr = equirect_utils.compute_resolution_from_equirect(self.hdr_dir, self.images_per_equirect)
            CONSOLE.log(f"Generating {self.images_per_equirect} {pers_size} sized images per equirectangular image")
            self.data, perspective_masks, perspective_hdrs = equirect_utils.generate_planar_projections_from_equirectangular(
                self.data, pers_size, self.images_per_equirect, self.mask_dir, hdr_dir=self.hdr_dir, crop_factor=self.crop_factor, 
                is_HDR=self.is_HDR, HDR_planar_image_size=pers_size_hdr
            )
            self.camera_type = "perspective"

        summary_log = []

        # Copy and downscale images
        if not self.skip_image_processing:
            # Copy images to output directory
            
            image_rename_map_paths = process_data_utils.copy_images(
                self.data,
                image_dir=self.image_dir,
                crop_factor=self.crop_factor,
                verbose=self.verbose,
                num_downscales=self.num_downscales,
            )
            
            if self.eval_data is not None:	
                eval_image_rename_map_paths = process_data_utils.copy_images(	
                    self.eval_data,	
                    image_dir=self.image_dir,	
                    crop_factor=self.crop_factor,	
                    image_prefix="frame_eval_",	
                    verbose=self.verbose,	
                    num_downscales=self.num_downscales,	
                )	
                image_rename_map_paths.update(eval_image_rename_map_paths)
            
            if perspective_masks is not None:
                (self.output_dir / "masks").mkdir(parents=True, exist_ok=True)
                mask_rename_map_paths = process_data_utils.copy_images(
                    perspective_masks, 
                    image_dir=self.output_dir / "masks", 
                    num_downscales=self.num_downscales, 
                    verbose=self.verbose, 
                    crop_factor=self.crop_factor
                )

            if perspective_hdrs is not None:
                (self.output_dir / "hdrs").mkdir(parents=True, exist_ok=True)
                copied_image_paths = process_data_utils.copy_images(
                    perspective_hdrs, 
                    image_dir=self.output_dir / "hdrs", 
                    verbose=self.verbose, 
                    num_downscales=self.num_downscales, 
                    crop_factor=self.crop_factor,
                    is_HDR = True
                )

            image_rename_map = dict((a.name, b.name) for a, b in image_rename_map_paths.items())
            num_frames = len(image_rename_map)
            summary_log.append(f"Starting with {num_frames} images")

            # Downscale images
            # summary_log.append(
            #     process_data_utils.downscale_images(self.image_dir, self.num_downscales, verbose=self.verbose)
            # )
            # # Downscale images
            # summary_log.append(
            #     process_data_utils.downscale_images((self.output_dir / "masks"), self.num_downscales, verbose=self.verbose)
            # )
        else:
            num_frames = len(process_data_utils.list_images(self.data))
            if num_frames == 0:
                raise RuntimeError("No usable images in the data folder.")
            summary_log.append(f"Starting with {num_frames} images")

        image_rename_map = None
        # Run COLMAP
        if not self.skip_colmap:
            require_cameras_exist = True
            if perspective_masks is not None:
                self._run_colmap(mask_path=(self.output_dir / "masks"))
            else:
                self._run_colmap()
            # Colmap uses renamed images
            image_rename_map = None

        # # Export depth maps
        image_id_to_depth_path, log_tmp = self._export_depth()
        summary_log += log_tmp

        if require_cameras_exist and not (self.absolute_colmap_model_path / "cameras.bin").exists():
            raise RuntimeError(f"Could not find existing COLMAP results ({self.colmap_model_path / 'cameras.bin'}).")

        summary_log += self._save_transforms(
            num_frames,
            image_id_to_depth_path,
            (self.output_dir / "masks") if perspective_masks is not None else None,
            (self.output_dir / "hdrs") if perspective_hdrs is not None else None,
            image_rename_map,
            self.is_HDR
        )

        CONSOLE.log("[bold green]:tada: :tada: :tada: All DONE :tada: :tada: :tada:")

        for summary in summary_log:
            CONSOLE.log(summary)
