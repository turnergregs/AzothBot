import numpy as np
import os
from PIL import Image
import random
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.tri import Triangulation, CubicTriInterpolator


class RandomEigenfunctionGenerator:
    def __init__(self, eigenfunctions_dir="eigenfunctions"):
        """
        Initialize the RandomEigenfunctionGenerator with the directory containing eigenfunction files.

        Args:
            eigenfunctions_dir: Directory containing eigenfunction files
        """
        self.eigenfunctions_dir = eigenfunctions_dir

        # Define element colors
        self.element_colors = {
            'blood': (255, 0, 0, 255),  # Red
            'sol': (249, 164, 16, 255),  # Gold
            'anima': (135, 105, 233, 255),  # Purple
            'dark': (0, 0, 0, 255),  # Black
            'light': (255, 255, 255, 255),  # White
            'all': (255, 255, 255, 255)  # White
        }

        # Define background color (near black)
        self.background_color = (12, 12, 12, 0)

        # Find all eigenfunction files in the directory
        self.eigenfunction_files = []
        for file in os.listdir(eigenfunctions_dir):
            if file.endswith("_eigenfunctions.npy"):
                base_name = file.replace("_eigenfunctions.npy", "")
                # Check if we have all required files
                if (os.path.exists(os.path.join(eigenfunctions_dir, f"{base_name}_eigenvalues.npy")) and
                        os.path.exists(os.path.join(eigenfunctions_dir, f"{base_name}_solver_data.npz"))):
                    self.eigenfunction_files.append(base_name)

        if not self.eigenfunction_files:
            raise FileNotFoundError("No eigenfunction files found in the specified directory")

    def _select_random_eigenfunction_set(self):
        """Select a random eigenfunction file set from the available ones"""
        base_filename = random.choice(self.eigenfunction_files)

        # Load the eigenfunction data
        eigenvalues = np.load(os.path.join(self.eigenfunctions_dir, f"{base_filename}_eigenvalues.npy"))
        eigenfunctions = np.load(os.path.join(self.eigenfunctions_dir, f"{base_filename}_eigenfunctions.npy"))

        # Load solver data for interpolation
        solver_data = np.load(os.path.join(self.eigenfunctions_dir, f"{base_filename}_solver_data.npz"))
        points = solver_data['points']
        elements = solver_data['elements']

        return base_filename, eigenvalues, eigenfunctions, points, elements

    def _create_interpolation_grid(self, points):
        """Create interpolation grid for the eigenfunction"""
        # Determine image size based on point extents
        x_min, x_max = points[:, 0].min(), points[:, 0].max()
        y_min, y_max = points[:, 1].min(), points[:, 1].max()

        # Add padding
        padding = 50
        width = int(x_max - x_min) + padding * 2
        height = int(y_max - y_min) + padding * 2

        # Create interpolation grid
        x = np.linspace(x_min - padding, x_max + padding, width)
        y = np.linspace(y_min - padding, y_max + padding, height)
        X, Y = np.meshgrid(x, y)

        return X, Y, width, height

    def _interpolate_eigenfunction(self, combined, points, elements, X, Y):
        """Interpolate eigenfunction onto regular grid"""
        # Create triangulation
        triang = Triangulation(points[:, 0], points[:, 1], elements)

        # Create interpolator
        interpolator = CubicTriInterpolator(triang, combined, kind='geom')

        # Initialize with zeros
        Z = np.zeros_like(X)

        # Get domain bounds
        x_min, x_max = points[:, 0].min(), points[:, 0].max()
        y_min, y_max = points[:, 1].min(), points[:, 1].max()

        # Create mask for points inside the domain
        X_mask = (X >= x_min) & (X <= x_max)
        Y_mask = (Y >= y_min) & (Y <= y_max)
        domain_mask = X_mask & Y_mask

        # Only interpolate within domain bounds
        X_interior = X[domain_mask]
        Y_interior = Y[domain_mask]

        # Interpolate eigenfunction
        Z_interior = interpolator(X_interior, Y_interior)

        # Update only the valid interpolated points
        Z[domain_mask] = Z_interior

        # Create base interpolator to mask outside the domain
        base_interpolator = CubicTriInterpolator(triang, np.ones_like(combined))
        domain_points = ~np.isnan(base_interpolator(X, Y))

        # Apply threshold for pattern
        pattern = np.zeros_like(Z, dtype=bool)  # Start with False
        pattern[domain_points] = (np.abs(Z[domain_points]) - 0.15) <= 0

        return pattern, domain_points

    def _apply_colors(self, pattern, domain_mask, element, width, height):
        """Apply colors to the pattern based on the selected element"""
        # Get element color
        if element not in self.element_colors:
            raise ValueError(f"Unknown element: {element}. Valid elements are: {list(self.element_colors.keys())}")

        element_color = self.element_colors[element]

        # Create RGBA image with background color
        image = np.zeros((height, width, 4), dtype=np.uint8)
        image[:, :] = element_color  # Set background color

        # Apply background color to pattern
        image[pattern] = self.background_color

        # Set alpha to 0 for points outside the domain
        image[~domain_mask] = self.background_color

        return image

    def create_symmetric_image(self, image_array):
        # Flip the image along the y-axis (horizontally)
        flipped = np.fliplr(image_array)

        # For perfect symmetry, we might want to use only half of the original image
        # and concatenate it with the flipped version of that half
        half_width = image_array.shape[1] // 2
        left_half = image_array[:, :half_width]
        right_half = np.fliplr(left_half)

        # Combine to create a perfectly symmetric image
        symmetric_image = np.concatenate((left_half, right_half), axis=1)

        return symmetric_image

    def generate_random_image(self, element, output_path=None):
        """
        Generate a random combination of eigenfunctions and save as an image.

        Args:
            element: The element color to use ('blood', 'sol', or 'anima')
            output_path: Path to save the image to (if None, a default name will be used)

        Returns:
            dict: Generation parameters
            str: Path to the saved image
        """
        # Select random eigenfunction set
        base_filename, eigenvalues, eigenfunctions, points, elements = self._select_random_eigenfunction_set()

        # Choose number of eigenfunctions to combine (2-4)
        n_funcs = np.random.randint(2, 5)

        # Randomly select eigenfunctions
        indices = np.random.choice(len(eigenvalues)//2, n_funcs, replace=False)

        # Generate random amplitudes (-1 to 1)
        amplitudes = np.random.uniform(-1, 1, n_funcs)

        # Combine eigenfunctions
        combined = np.zeros_like(eigenfunctions[:, 0])
        for idx, amp in zip(indices, amplitudes):
            combined += amp * eigenfunctions[:, idx]

        # Normalize
        combined = combined / np.max(np.abs(combined))

        # Create interpolation grid
        X, Y, width, height = self._create_interpolation_grid(points)

        # Interpolate the combined eigenfunction
        pattern, domain_mask = self._interpolate_eigenfunction(combined, points, elements, X, Y)

        # Apply colors
        image_array = self._apply_colors(pattern, domain_mask, element, width, height)

        image_array = self.create_symmetric_image(image_array)
        # Convert to PIL Image and rotate/flip to correct orientation
        image = Image.fromarray(image_array)
        # image = image.transpose(Image.ROTATE_90)
        # image = image.transpose(Image.FLIP_LEFT_RIGHT)

        # Generate output path if not provided
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = "combinations"
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"{base_filename}_{element}_combo_{timestamp}.png")

        # Save image
        image.save(output_path)

        # Prepare generation parameters
        generation_params = {
            "base_file": base_filename,
            "element": element,
            "eigenfunction_indices": indices.tolist(),
            "amplitudes": amplitudes.tolist(),
            "timestamp": datetime.now().isoformat()
        }

        return generation_params, output_path


# Example usage
if __name__ == "__main__":
    generator = RandomEigenfunctionGenerator()
    params, image_path = generator.generate_random_image("anima")
    print(f"Generated image saved to: {image_path}")
    print("Generation parameters:", params)