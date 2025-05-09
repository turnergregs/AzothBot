from PIL import Image, ImageDraw, ImageFont, ImageOps
import json
import os
from pathlib import Path
import math
import numpy as np
from PIL.ImageSequence import Iterator

FONT_PATH = os.path.join("assets", "fonts", "Aldrich-Regular.ttf")
ICON_DIR = os.path.join("assets", "icons")

DOWNLOADED_IMAGES_DIR = os.path.join("assets", "downloaded_images")
RENDERED_CARDS_DIR = os.path.join("assets", "rendered_cards")


class CardRenderer:
    def __init__(self, ppi=900, bleed_mm=8.5):
        # Standard playing card size is 63.5mm x 88.9mm
        self.card_width_mm = 60.5
        self.card_height_mm = 85.9
        self.bleed_mm = bleed_mm
        self.ppi = min(max(300, ppi), 900)  # Constrain between 300 and 900 ppi

        # Convert measurements to pixels
        mm_to_inch = 0.0393701
        self.px_per_mm = self.ppi * mm_to_inch
        self.width = round((self.card_width_mm + (2 * self.bleed_mm)) * self.px_per_mm)
        self.height = round((self.card_height_mm + (2 * self.bleed_mm)) * self.px_per_mm)
        self.height = 3330
        self.width = 2448

        self.border_width = 40  # Fixed 10px border width

        # Load fonts
        base_size = round(self.width / 16)  # Starting title font size
        self.valence_font = ImageFont.truetype(FONT_PATH, base_size)
        self.text_font = ImageFont.truetype(FONT_PATH, round(base_size * 0.9))
        # Colors
        # self.light_mode = {
        #     'border': (0, 0, 0, 255),
        #     'text': (0, 0, 0, 255),
        #     'background': (255, 255, 255, 255),
        #     'valence': (0, 0, 0, 255)
        # }
        self.light_mode = {
            'border': (225, 225, 225, 255),
            'text': (225, 225, 225, 255),
            'background': (12, 12, 12, 255),
            'valence': (12, 12, 12, 255)
        }

        self.dark_mode = {
            'border': (255, 255, 255, 255),
            'text': (225, 225, 225, 255),
            'background': (12, 12, 12, 255),
            'valence': (12, 12, 12, 255)
        }

        self.element_colors = {
            'blood': (255, 0, 0, 255),  # Red
            'sol': (249, 164, 16, 255),  # Gold
            'anima': (135, 105, 233, 255),  # Purple
        }

        self.placeholder_dict = {
            'blood': "placeholder_blood.png",
            'sol': "placeholder_sol.png",
            'anima': "placeholder_anima.png"
        }



    def get_placeholder_image(self, card_data):
        return self.placeholder_dict[card_data['element']]

    def draw_valence_shape(self, draw, image, element, center_x, center_y, radius, colors):
        """Draw the valence shape based on the element type."""
        try:
            # Load the appropriate icon based on element
            icon_path = fr"{ICON_DIR}/{element.capitalize()}.png"
            icon = Image.open(icon_path)

            # Convert to RGBA if it isn't already
            if icon.mode != 'RGBA':
                icon = icon.convert('RGBA')

            # Calculate the desired size (diameter of the original circle)
            icon_size = int(radius * 2)

            # Resize the icon while maintaining aspect ratio
            icon.thumbnail((int(icon.size[0]*0.9), int(icon.size[1]*0.9)), Image.LANCZOS)

            # Calculate position to center the icon
            paste_x = int(center_x - icon.width / 2)
            paste_y = int(center_y - icon.height / 2)

            # Paste the icon onto the main image
            main_image = image
            main_image.paste(icon, (paste_x, paste_y), icon)

        except Exception as e:
            print(f"Error loading or pasting icon: {e}")
            # Fallback to original circle if there's an error
            draw.ellipse(
                [center_x - radius, center_y - radius,
                 center_x + radius, center_y + radius],
                outline=self.element_colors[element],
                fill=self.element_colors[element],
                width=self.border_width
            )

    def get_predominant_color(self, image):
        """
        Determine if black or white is predominant in an RGBA image.
        Assumes image is a numpy array with shape (height, width, 4) for RGBA.
        """
        # Check if pixels are black (0,0,0,255) or white (255,255,255,255)
        black_pixels = np.all(image[:, :, :3] <= 14, axis=2) & (image[:, :, 3] == 255)
        white_pixels = np.all(image[:, :, :3] == 255, axis=2) & (image[:, :, 3] == 255)

        black_count = np.sum(black_pixels)
        white_count = np.sum(white_pixels)

        total_pixels = image.shape[0] * image.shape[1]
        if black_count > white_count or black_count + white_count <= 10000:
            return "black", (black_count / total_pixels) * 100
        else:
            return "white", (white_count / total_pixels) * 100



    def set_black__white_to_off_black_white(self, image, threshold=100):
        """
        Invert black and white pixels in an RGBA image, using a threshold to catch near-black pixels.

        Args:
            image: RGBA numpy array
            threshold: Integer 0-255. Pixels with RGB values all below this are considered "black"
                      Higher threshold will catch more dark grays
        """
        result = image.copy()

        # Find near-black pixels (all RGB values below threshold)
        # We only look at RGB channels (not alpha) when determining darkness
        black_pixels = np.all(image[:, :, :3] <= threshold, axis=2)

        # Find white pixels (keeping original strict white detection)
        white_pixels = np.all(image[:, :, :3] >= 255- threshold, axis=2)

        # Set black/dark pixels to white
        result[black_pixels] = [12, 12, 12, 255]
        # Set white pixels to black
        result[white_pixels] = [225, 225, 225, 255]

        return result

    def invert_black_white(self, image, threshold=100):
        """
        Invert black and white pixels in an RGBA image, using a threshold to catch near-black pixels.

        Args:
            image: RGBA numpy array
            threshold: Integer 0-255. Pixels with RGB values all below this are considered "black"
                      Higher threshold will catch more dark grays
        """
        result = image.copy()

        # Find near-black pixels (all RGB values below threshold)
        # We only look at RGB channels (not alpha) when determining darkness
        black_pixels = np.all(image[:, :, :3] <= threshold, axis=2)

        # Find white pixels (keeping original strict white detection)
        white_pixels = np.all(image[:, :, :3] >= 255- threshold, axis=2)

        # Set black/dark pixels to white
        result[black_pixels] = [255, 255, 255, 255]
        # Set white pixels to black
        result[white_pixels] = [0, 0, 0, 255]

        return result


    def process_frame(self, input_image, target_size, padding_ratio=0.1, card_data=None):
        """Process a single frame: find pattern bounds, crop to square, and resize with padding"""
        # Convert to RGB if not already
        if input_image.mode != 'RGBA':
            input_image = input_image.convert('RGB')

        # Convert image to numpy array for processing
        img_array = np.array(input_image)

        # Create mask for non-black pixels
        # Check if any channel has value greater than 0
        is_dark_mode = "Arcana" in card_data.get('type', '').split()
        is_dark_mode = True
        background_color, _ = self.get_predominant_color(img_array)
        if is_dark_mode:
            if background_color == "white":
                img_array = self.invert_black_white(img_array)


            pattern_mask = np.any(img_array[:, :, 0:3] > 0, axis=2)
        else:
            if background_color == 'black':
                img_array = self.invert_black_white(img_array)
            pattern_mask = np.any(img_array[:, :, 0:3] < 255, axis=2)

        img_array = self.set_black__white_to_off_black_white(img_array)

        input_image = Image.fromarray(img_array)
        import matplotlib.pyplot as plt
        # plt.imshow(input_image)
        # plt.show()

        # Find the bounding box of the pattern
        rows = np.any(pattern_mask, axis=1)
        cols = np.any(pattern_mask, axis=0)
        if not np.any(rows) or not np.any(cols):
            # If no pattern found, return the original center crop
            return self.process_frame_original(input_image, target_size, padding_ratio)

        # Get the pattern boundaries
        ymin, ymax = np.where(rows)[0][[0, -1]]
        xmin, xmax = np.where(cols)[0][[0, -1]]

        # Add small padding around the pattern (10% of pattern size)
        pattern_height = ymax - ymin
        pattern_width = xmax - xmin
        padding = int(max(pattern_height, pattern_width) * 0.1)

        # Expand the bounds with padding
        ymin = max(0, ymin - padding)
        ymax = min(input_image.height, ymax + padding)
        xmin = max(0, xmin - padding)
        xmax = min(input_image.width, xmax + padding)

        # Calculate the square bounds
        pattern_size = max(ymax - ymin, xmax - xmin)

        # Ensure the square is centered on the pattern
        x_center = (xmin + xmax) // 2
        y_center = (ymin + ymax) // 2

        # Calculate square bounds
        half_size = pattern_size // 2
        square_xmin = max(0, x_center - half_size)
        square_ymin = max(0, y_center - half_size)
        square_xmax = min(input_image.width, square_xmin + pattern_size)
        square_ymax = min(input_image.height, square_ymin + pattern_size)

        # Ensure we maintain a square even if we hit image boundaries
        width = square_xmax - square_xmin
        height = square_ymax - square_ymin
        if width != height:
            new_size = max(width, height)
            if width < new_size:
                diff = new_size - width
                square_xmin = max(0, square_xmin - diff // 2)
                square_xmax = min(input_image.width, square_xmin + new_size)
                if square_xmax - square_xmin < new_size:  # Hit right boundary
                    square_xmin = max(0, square_xmax - new_size)
            if height < new_size:
                diff = new_size - height
                square_ymin = max(0, square_ymin - diff // 2)
                square_ymax = min(input_image.height, square_ymin + new_size)
                if square_ymax - square_ymin < new_size:  # Hit bottom boundary
                    square_ymin = max(0, square_ymax - new_size)

        # Crop to the square bounds
        cropped_image = input_image.crop((square_xmin, square_ymin, square_xmax, square_ymax))
        # plt.imshow(cropped_image)
        # plt.show()


        def maintain_aspect_ratio(image, target_size):
            original_width, original_height = image.size
            target_width, target_height = target_size

            # Calculate aspect ratios
            original_aspect = original_width / original_height
            target_aspect = target_width / target_height

            if target_aspect > original_aspect:
                # Width is the constraining factor
                new_width = target_height * original_aspect
                new_height = target_height
            else:
                # Height is the constraining factor
                new_width = target_width
                new_height = target_width / original_aspect

            return image.resize((int(new_width), int(new_height)), Image.LANCZOS)
        # Resize maintaining aspect ratio

        padding = round(target_size[0] * padding_ratio)
        target_with_padding = (target_size[0] - 2 * padding, target_size[1] - 2 * padding)

        cropped_image  = maintain_aspect_ratio(cropped_image, target_with_padding)

        # cropped_image.save("icons/" + card_data['name'].replace(" ", "_") + '_icon.png')

        # plt.imshow(cropped_image)
        # plt.show()

        # Create transparent base image
        final_image = Image.new('RGBA', (target_size[0], target_size[1] + 1000), (12, 12, 12, 0))

        # Calculate text dimensions if card_data is provided
        margin = round(self.px_per_mm * self.bleed_mm)
        text_offset = 0

        if card_data and 'text' in card_data:
            # Get text dimensions to adjust image position
            card_text = card_data['text']
            text_box_width = target_size[0] - (2 * margin) - (4 * self.border_width) - 2 * self.px_per_mm * 3

            # Helper function to calculate wrapped lines
            def get_wrapped_lines(text, font, max_width):
                words = text.split()
                lines = []
                current_line = []

                for word in words:
                    test_line = ' '.join(current_line + [word])
                    bbox = font.getbbox(test_line)
                    width = bbox[2] - bbox[0]

                    if width <= max_width:
                        current_line.append(word)
                    else:
                        if current_line:
                            lines.append(' '.join(current_line))
                        current_line = [word]

                if current_line:
                    lines.append(' '.join(current_line))
                return lines

            # Calculate number of lines
            wrapped_lines = get_wrapped_lines(card_text, self.text_font, text_box_width)
            line_count = len(wrapped_lines)

            # Calculate offset based on line count
            text_offset = min(line_count * 20, 100)  # Adjust these values to control the shift

        # Calculate paste position with text offset
        paste_x = (target_size[0] - cropped_image.size[0]) // 2
        paste_y = (target_size[1] - cropped_image.size[1]) // 2 - text_offset + 500

        # Create a rounded rectangle mask
        mask = Image.new('L', (target_size[0], target_size[1] + 1000), 0)
        mask_draw = ImageDraw.Draw(mask)
        corner_radius = round(target_size[0] * 0.05)  # Match the card corner radius

        # Draw rounded rectangle on mask
        mask_draw.rounded_rectangle(
            [0, 0, target_size[0], target_size[1] + 1000 ],
            corner_radius,
            fill=255,

        )
        # Create a new image with the cropped content and apply the mask
        if is_dark_mode:
            temp_image = Image.new('RGBA', (target_size[0], target_size[1] + 1000), (12, 12, 12, 0))
        else:
            temp_image = Image.new('RGBA', (target_size[0], target_size[1] + 1000), (225, 225, 225, 0))
        temp_image.paste(cropped_image, (paste_x, paste_y))
        temp_image.putalpha(mask)

        # import matplotlib.pyplot as plt
        # plt.imshow(temp_image)
        # plt.show()
        # plt.show()
        # Paste the masked image onto the final transparent image
        final_image = Image.alpha_composite(final_image, temp_image)

        # import matplotlib.pyplot as plt
        # plt.imshow(final_image)
        # plt.show()
        import matplotlib.pyplot as plt



        return final_image, (square_xmin, square_ymin, square_xmax, square_ymax), (paste_x, paste_y)

    def process_frame_original(self, input_image, target_size, padding_ratio=0.01):
        """Original processing method as fallback"""
        # Convert to square by cropping to center
        min_dim = min(input_image.size)
        left = (input_image.size[0] - min_dim) // 2
        top = (input_image.size[1] - min_dim) // 2
        right = left + min_dim
        bottom = top + min_dim
        square_image = input_image.crop((left, top, right, bottom))

        # Calculate target size with padding
        padding = round(target_size[0] * padding_ratio)
        target_with_padding = (target_size[0] - 2 * padding, target_size[1] - 2 * padding)

        # Resize maintaining aspect ratio
        square_image.thumbnail(target_with_padding, Image.LANCZOS)

        # Create new image with padding
        final_image = Image.new('RGBA', target_size, (0, 0, 0, 0))
        paste_x = (target_size[0] - square_image.size[0]) // 2
        paste_y = (target_size[1] - square_image.size[1]) // 2
        final_image.paste(square_image, (paste_x, paste_y))

        return final_image, (left, top, right, bottom), (paste_x, paste_y)

    def draw_wrapped_text(self, draw, text, x, y, box_width, box_height, start_font_size, font_path, fill_color):
        """
        Draw wrapped text that scales to fit within a given box, at the specified position.
        """

        def get_wrapped_lines(text, font, max_width):
            words = text.split()
            lines = []
            current_line = []

            for word in words:
                test_line = ' '.join(current_line + [word])
                bbox = font.getbbox(test_line)
                width = bbox[2] - bbox[0]

                if width <= max_width:
                    current_line.append(word)
                else:
                    if current_line:
                        lines.append(' '.join(current_line))
                    current_line = [word]

            if current_line:
                lines.append(' '.join(current_line))

            return lines

        def compute_text_height(lines, font):
            sample_bbox = font.getbbox('Ay')
            line_height = sample_bbox[3] - sample_bbox[1]
            line_spacing = line_height * 0.2
            return len(lines) * (line_height + line_spacing)

        # Auto-scale font size
        font_size = start_font_size
        min_font_size = 12

        while font_size >= min_font_size:
            font = ImageFont.truetype(font_path, font_size)
            lines = get_wrapped_lines(text, font, box_width)
            total_height = compute_text_height(lines, font)

            if total_height <= box_height:
                break

            font_size -= 1

        font = ImageFont.truetype(font_path, font_size)
        lines = get_wrapped_lines(text, font, box_width)

        # Get line height for positioning
        sample_bbox = font.getbbox('Ay')
        line_height = sample_bbox[3] - sample_bbox[1]
        line_spacing = line_height * 0.2

        # Calculate total text block height
        total_text_height = len(lines) * (line_height + line_spacing)

        # Start at the specified y position
        current_y = y - (total_text_height / 2)  # Center the text block vertically around the specified y

        # Draw each line centered horizontally around the specified x
        for line in lines:
            bbox = font.getbbox(line)
            line_width = bbox[2] - bbox[0]
            line_x = x - (line_width / 2)  # Center around specified x

            draw.text((line_x, current_y), line, font=font, fill=fill_color)
            current_y += line_height + line_spacing

    def render_card(self, card_data, output_dir="output", transparent_outside=False):
        """Render a card and save it along with its data.

        Args:
            card_data (dict): Card data including name, valence, type, etc.
            output_dir (str): Directory to save the output files
            transparent_outside (bool): If True, only fill background within the rounded rectangle
        """
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        # Generate base filename from card name
        base_filename = card_data['name'].lower().replace(' ', '_')
        image_filename = f"{base_filename}.png"
        json_filename = f"{base_filename}.json"
        output_path = os.path.join(output_dir, image_filename)
        json_path = os.path.join(output_dir, json_filename)

        # Determine color scheme
        is_dark_mode = "Arcana" in card_data.get('type', '').split()
        colors = self.dark_mode if is_dark_mode else self.light_mode

        # Calculate image area
        border_width = round(self.px_per_mm * 0.5)
        margin = round(self.px_per_mm * self.bleed_mm)
        image_margin = margin + border_width * 2
        title_margin = 500
        image_area = (
            self.width - (image_margin * 2),
            self.height - (image_margin * 2) - title_margin * 2
        )

        # Process image if provided
        processed_frames = []
        crop_params = None
        paste_pos = None
        is_animated = False

        if 'image' in card_data and card_data['image']:
            try:
                source_image = Image.open(os.path.join(DOWNLOADED_IMAGES_DIR, card_data['image']))
                is_animated = hasattr(source_image, 'is_animated') and source_image.is_animated

                # Process first frame to get parameters
                first_frame = source_image.copy()
                if is_animated:
                    first_frame.seek(0)
                processed_frame, crop_params, paste_pos = self.process_frame(first_frame, image_area, card_data=card_data)
                processed_frames.append(processed_frame)

                # Process remaining frames if animated
                if is_animated:
                    for frame_idx in range(1, source_image.n_frames):
                        source_image.seek(frame_idx)
                        frame = source_image.copy()
                        # Use the same processing function with the same parameters
                        processed_frame, _, _ = self.process_frame(
                            frame.crop(crop_params),  # Apply initial crop
                            image_area,  # Same target size
                            padding_ratio=0.1,  # Same padding
                            card_data = card_data
                        )
                        processed_frames.append(processed_frame)

            except Exception as e:
                print(f"Error loading image: {e}")
        # else:
        #     local_temp_image = self.get_placeholder_image(card_data)
        #     source_image = r'C:\Users\Caleb\PycharmProjects\azoth\images/' + local_temp_image
        #     is_animated = hasattr(source_image, 'is_animated') and source_image.is_animated
        #
        #     # Process first frame to get parameters
        #     first_frame = source_image.copy()
        #     if is_animated:
        #         first_frame.seek(0)
        #     processed_frame, crop_params, paste_pos = self.process_frame(first_frame, image_area, card_data=card_data)
        #     processed_frames.append(processed_frame)
        #
        #     # Process remaining frames if animated
        #     if is_animated:
        #         for frame_idx in range(1, source_image.n_frames):
        #             source_image.seek(frame_idx)
        #             frame = source_image.copy()
        #             # Use the same processing function with the same parameters
        #             processed_frame, _, _ = self.process_frame(
        #                 frame.crop(crop_params),  # Apply initial crop
        #                 image_area,  # Same target size
        #                 padding_ratio=0.1,  # Same padding
        #                 card_data=card_data
        #             )
        #             processed_frames.append(processed_frame)

        def render_single_frame(frame_image=None):
            if transparent_outside:
                # Create fully transparent base image
                image = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))

                # Create a mask for the rounded rectangle with exact same parameters as border
                mask = Image.new('L', (self.width, self.height), 0)
                mask_draw = ImageDraw.Draw(mask)

                # Use same radius and coordinates as the border
                corner_radius = round(self.width * 0.05)
                rect_bounds = [margin, margin, self.width - margin, self.height - margin]

                # Draw the mask slightly smaller than the border to prevent bleeding
                inset = self.border_width // 2
                mask_bounds = [
                    rect_bounds[0] + inset,
                    rect_bounds[1] + inset,
                    rect_bounds[2] - inset,
                    rect_bounds[3] - inset
                ]
                mask_draw.rounded_rectangle(
                    mask_bounds,
                    corner_radius,
                    fill=255
                )

                # Create background color layer
                bg_layer = Image.new('RGBA', (self.width, self.height), colors['background'])
                # Apply the mask to the background
                bg_layer.putalpha(mask)

                # Composite the background onto the transparent base
                image = Image.alpha_composite(image, bg_layer)
            else:
                # Create fully transparent base image
                image = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))

                # Create a mask for the rounded rectangle with exact same parameters as border
                mask = Image.new('L', (self.width, self.height), 0)
                mask_draw = ImageDraw.Draw(mask)

                # Use same radius and coordinates as the border
                corner_radius = round(self.width * 0.05)
                rect_bounds = [margin/3.2, margin/3.2, self.width - margin/3.2, self.height - margin/3.2]

                # Draw the mask slightly smaller than the border to prevent bleeding
                inset = self.border_width // 2
                mask_bounds = [
                    rect_bounds[0] + inset,
                    rect_bounds[1] + inset,
                    rect_bounds[2] - inset,
                    rect_bounds[3] - inset
                ]
                mask_draw.rounded_rectangle(
                    mask_bounds,
                    corner_radius,
                    fill=255
                )

                # Create background color layer
                bg_layer = Image.new('RGBA', (self.width, self.height), colors['background'])
                # Apply the mask to the background
                bg_layer.putalpha(mask)

                # Composite the background onto the transparent base
                image = Image.alpha_composite(image, bg_layer)

            draw = ImageDraw.Draw(image)

            # Place processed image if available
            if frame_image:
                # Convert frame_image back to RGBA if it's not already
                if frame_image.mode != 'RGBA':
                    frame_image = frame_image.convert('RGBA')
                # import matplotlib.pyplot as plt
                # plt.imshow(frame_image)
                # plt.show()
                # Simple paste without using the image as its own mask
                image.paste(frame_image, (image_margin, image_margin), frame_image)

                # image = Image.alpha_composite(image, frame_image)
                # import matplotlib.pyplot as plt
                # plt.imshow(image)
                # plt.show()
            if True:
                # Draw outer border with color based on element
                border_color = self.element_colors.get(card_data.get('element', 'sol'), colors['border'])
                draw.rounded_rectangle(
                    [margin, margin, self.width - margin, self.height - margin],
                    round(self.width * 0.05),
                    outline=border_color,
                    width=self.border_width
                )

                # Draw valence shape if valence exists
                circle_radius = round(self.width * 0.064)
                if 'valence' in card_data and card_data['valence'] is not None:
                    # Center horizontally
                    circle_center_x = self.width / 2
                    # Position vertically so circle center aligns with border
                    circle_center_y = margin + self.border_width / 2

                    # Draw the appropriate valence shape based on element
                    element = card_data.get('element', 'sol')  # Default to sol if no element specified
                    self.draw_valence_shape(
                        draw,
                        image,
                        element,
                        circle_center_x,
                        circle_center_y,
                        circle_radius,
                        colors
                    )

                # Draw text with fake bold effect
                def draw_semibold_text(text, x, y, font, fill):
                    # Small offset for semi-bold effect (adjust the offset to control boldness)
                    offset = max(1, round(font.size * 0.01))  # Scale with font size

                    # Draw multiple times with small offsets
                    draw.text((x + offset, y), text, font=font, fill=fill)
                    draw.text((x - offset, y), text, font=font, fill=fill)
                    draw.text((x, y + offset), text, font=font, fill=fill)
                    draw.text((x, y - offset), text, font=font, fill=fill)
                    # Finally draw the main text
                    draw.text((x, y), text, font=font, fill=fill)

                # Center valence text in circle with semi-bold effect
                valence_text = str(card_data['valence'])
                valence_bbox = draw.textbbox((0, 0), valence_text, font=self.valence_font)
                valence_width = valence_bbox[2] - valence_bbox[0]
                valence_height = valence_bbox[3] - valence_bbox[1]
                valence_x = circle_center_x - valence_width / 2
                valence_y = circle_center_y - valence_height / 2
                draw_semibold_text(
                    valence_text,
                    valence_x,
                    valence_y,
                    self.valence_font,
                    colors['valence']
                )

                # Draw card name with auto-scaling
                name_text = card_data['name']

                # Calculate maximum allowed width (with padding)
                max_title_width = self.width - (2 * margin) - (4 * self.border_width) - 2 * self.px_per_mm * 3

                # Start with default font size and try progressively smaller sizes
                base_size = round(self.width / 15)
                min_size = round(base_size * 0.5)
                current_font = None

                # Try sizes from largest to smallest until finding one that fits
                for size in range(base_size, min_size - 1, -1):
                    test_font = ImageFont.truetype(FONT_PATH, size)
                    test_bbox = draw.textbbox((0, 0), name_text, font=test_font)
                    test_width = test_bbox[2] - test_bbox[0]

                    if test_width <= max_title_width:
                        current_font = test_font
                        break

                # If no suitable size found, use minimum size
                if current_font is None:
                    current_font = ImageFont.truetype(FONT_PATH, min_size)

                # Draw card name with semibold effect
                name_bbox = draw.textbbox((0, 0), name_text, font=current_font)
                name_width = name_bbox[2] - name_bbox[0]
                name_x = (self.width - name_width) / 2
                name_y = margin + (circle_radius * 1.8)
                draw_semibold_text(
                    name_text,
                    name_x,
                    name_y,
                    current_font,
                    colors['text']
                )

                if 'text' in card_data and card_data['text']:
                    text = card_data['text']

                    # Calculate the maximum width for the text
                    text_box_width = self.width - (2 * margin) - (4 * self.border_width) - 2 * self.px_per_mm * 3
                    text_box_height = self.height * 0.3  # Height of the text box

                    # Use the original text_x and text_y positions
                    text_bbox = draw.textbbox((0, 0), text, font=self.text_font)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_x = self.width / 2  # Center position
                    text_y = self.height * 0.80  # Original vertical position

                    # Draw the wrapped and scaled text at the specified position
                    self.draw_wrapped_text(
                        draw,
                        text,
                        text_x,
                        text_y,
                        text_box_width,
                        text_box_height,
                        self.text_font.size,
                        FONT_PATH,
                        colors['text']
                    )

            return image

        # Save static version (using first frame if animated)
        static_image = render_single_frame(processed_frames[0] if processed_frames else None)

        if is_animated:
            # Save static version
            static_output = os.path.join(output_dir, f"{base_filename}_static.png")
            static_image.save(static_output, 'PNG')

            # Save animated version
            frames = [render_single_frame(frame) for frame in processed_frames]
            frames[0].save(
                output_path,
                save_all=True,
                append_images=frames[1:],
                duration=source_image.info.get('duration', 100),
                loop=0,
                format='GIF'
            )
        else:
            static_image.save(output_path, 'PNG')

        # # Save card data as JSON
        # card_info = {
        #     **card_data,  # Include all original card data
        #     'files': {
        #         'image': image_filename,
        #         'static_image': f"{base_filename}_static.png" if is_animated else image_filename
        #     },
        #     'render_info': {
        #         'ppi': self.ppi,
        #         'bleed_mm': self.bleed_mm,
        #         'width_px': self.width,
        #         'height_px': self.height,
        #         'timestamp': datetime.datetime.now().isoformat()
        #     }
        # }

        # with open(json_path, 'w', encoding='utf-8') as f:
        #     json.dump(card_info, indent=2, ensure_ascii=False)


    def create_tiled_image(self, cards, output_path):
        """
        Creates a tiled image containing all cards in a widescreen-like aspect ratio.

        Args:
            cards (list): List of card data dictionaries
            output_path (str): Path to save the tiled image
        """
        import math

        # First render all cards if they haven't been rendered yet
        card_images = []
        for card in cards:
            # Generate filename based on card name
            base_filename = card['name'].lower().replace(' ', '_')
            image_path = os.path.join(RENDERED_CARDS_DIR, f"{base_filename}.png")

            # Load the card image if it exists, otherwise render it
            if os.path.exists(image_path):
                card_images.append(Image.open(image_path))
            else:
                # Render the card and get the image
                self.render_card(card, RENDERED_CARDS_DIR)
                card_images.append(Image.open(image_path))

        num_cards = len(card_images)
        if num_cards == 0:
            raise ValueError("No cards provided")

        # Calculate optimal grid dimensions for 16:9 aspect ratio
        target_ratio = 16 / 9
        num_cols = math.ceil(math.sqrt(num_cards * target_ratio))
        num_rows = math.ceil(num_cards / num_cols)

        # Create the tiled image with padding
        padding = 20  # Pixels between cards
        tile_width = card_images[0].width
        tile_height = card_images[0].height

        total_width = num_cols * tile_width + (num_cols - 1) * padding
        total_height = num_rows * tile_height + (num_rows - 1) * padding

        # Create a new image with a black background
        tiled_image = Image.new('RGBA', (total_width, total_height), (0, 0, 0, 255))

        # Place each card in the grid
        for idx, card_image in enumerate(card_images):
            row = idx // num_cols
            col = idx % num_cols

            x = col * (tile_width + padding)
            y = row * (tile_height + padding)

            # Create a temporary image for this position
            temp_image = Image.new('RGBA', tiled_image.size, (0, 0, 0, 0))
            temp_image.paste(card_image, (x, y))

            # Composite the images
            tiled_image = Image.alpha_composite(tiled_image, temp_image)

        # Save the tiled image
        tiled_image.save(output_path, 'PNG')
        return tiled_image

    def create_sample_hand(self, cards, output_path, num_cards=6, spread_angle=30):
        """
        Creates an image of a sample hand of cards, arranged in a fan layout.

        Args:
            cards (list): List of card data dictionaries to sample from
            output_path (str): Path to save the hand image
            num_cards (int): Number of cards to draw (default 5)
            spread_angle (float): Total angle of the fan spread in degrees (default 30)
        """
        import random
        import math
        num_cards = min(num_cards, len(cards))
        # Randomly select cards
        selected_cards = random.sample(cards, min(num_cards, len(cards)))

        # Load or render the selected cards
        card_images = []
        for card in selected_cards:
            base_filename = card['name'].lower().replace(' ', '_')
            image_path = os.path.join(RENDERED_CARDS_DIR, f"{base_filename}.png")

            if os.path.exists(image_path):
                card_images.append(Image.open(image_path))
            else:
                self.render_card(card, RENDERED_CARDS_DIR)
                card_images.append(Image.open(image_path))

        # Calculate dimensions needed for the fan layout
        card_width = card_images[0].width
        card_height = card_images[0].height

        # Calculate the radius for the fan (20% wider)
        radius = card_width * 6 * num_cards/5  # Increased from 4 to 4.8 for wider spread

        # Calculate the total width and height needed
        angle_step = spread_angle / (num_cards - 1) if num_cards > 1 else 0
        start_angle = -spread_angle / 2

        # Calculate bounding box for the entire fan
        min_x = float('inf')
        max_x = float('-inf')
        min_y = float('inf')
        max_y = float('-inf')

        # Calculate positions for each card
        card_positions = []
        for i in range(num_cards):
            angle = math.radians(start_angle + (i * angle_step))

            # Calculate position
            x = -math.sin(angle) * radius
            y = -math.cos(angle) * radius  # Negative cosine creates upward curve for middle cards

            # Update bounding box
            min_x = min(min_x, x)
            max_x = max(max_x, x + card_width)
            min_y = min(min_y, y)
            max_y = max(max_y, y + card_height)

            card_positions.append((x, y, angle))

        # Create the final image with increased padding
        padding = 350  # Increased from 50 to 150 for more space around cards
        width = int(max_x - min_x + padding * 2)
        height = int(max_y - min_y + padding * 2)

        # Calculate scaling factor to make the width 1920 pixels
        scale_factor = 1920 / width
        final_width = 1920
        final_height = int(height * scale_factor)

        # Create a new image with a black background
        hand_image = Image.new('RGBA', (width, height), (0, 0, 0, 0))

        # Place each card with rotation
        for (x, y, angle), card_image in zip(card_positions, card_images):
            # Adjust positions relative to bounding box
            adjusted_x = int(x - min_x + padding)
            adjusted_y = int(y - min_y + padding)

            # Rotate the card
            rotated_card = card_image.rotate(math.degrees(angle), expand=True, resample=Image.BICUBIC)

            # Create a mask for smooth blending
            if rotated_card.mode != 'RGBA':
                rotated_card = rotated_card.convert('RGBA')

            # Calculate paste position accounting for rotation expansion
            paste_x = adjusted_x - (rotated_card.width - card_width) // 2
            paste_y = adjusted_y - (rotated_card.height - card_height) // 2

            # Paste the rotated card
            hand_image.paste(rotated_card, (paste_x, paste_y), rotated_card)

        # Scale the image down to 1920px width
        scaled_image = hand_image.resize((final_width, final_height), Image.LANCZOS)

        # Save the final scaled image
        scaled_image.save(output_path, 'PNG')
        return scaled_image

    def create_card_grid(self, cards, output_path, num_cards=None):
        """
        Creates an image of multiple cards arranged in a grid layout.
        The grid aims for a widescreen aspect ratio when possible.

        Args:
            cards (list): List of card data dictionaries to display
            output_path (str): Path to save the grid image
            num_cards (int, optional): Number of cards to include, defaults to all cards provided
        """
        # Determine number of cards to display
        if num_cards is None or num_cards > len(cards):
            num_cards = len(cards)

        selected_cards = cards[:num_cards]

        # Load or render the selected cards
        card_images = []
        for card in selected_cards:
            base_filename = card['name'].lower().replace(' ', '_')
            image_path = os.path.join(RENDERED_CARDS_DIR, f"{base_filename}.png")

            try:
                if os.path.exists(image_path):
                    card_images.append(Image.open(image_path))
                else:
                    # Render the card if it doesn't exist yet
                    self.render_card(card, RENDERED_CARDS_DIR)
                    card_images.append(Image.open(image_path))
            except Exception as e:
                print(f"Error loading card image for {card['name']}: {str(e)}")
                # Continue with other cards if one fails
                continue

        # If no valid card images, exit early
        if not card_images:
            raise ValueError("No valid card images could be loaded")

        # Get card dimensions from the first card
        card_width = card_images[0].width
        card_height = card_images[0].height

        # Calculate ideal grid dimensions for widescreen aspect ratio (16:9)
        # Target ratio is 16:9 = 1.78:1
        target_ratio = 16 / 9

        # Find the best grid arrangement (closest to widescreen)
        best_cols = 1
        best_rows = num_cards
        best_ratio_diff = float('inf')

        # Try different column counts to find the optimal arrangement
        for cols in range(1, num_cards + 1):
            rows = (num_cards + cols - 1) // cols  # Ceiling division to ensure all cards fit

            # Calculate the aspect ratio of this grid
            grid_ratio = (cols * card_width) / (rows * card_height)
            ratio_diff = abs(grid_ratio - target_ratio)

            # Update if this arrangement is closer to target ratio
            if ratio_diff < best_ratio_diff:
                best_ratio_diff = ratio_diff
                best_cols = cols
                best_rows = rows

        # Use the optimized grid dimensions
        cols = best_cols
        rows = best_rows

        # Calculate padding and spacing
        padding = 50  # Padding around the entire grid
        spacing = 20  # Space between cards

        # Calculate total dimensions
        total_width = cols * card_width + (cols - 1) * spacing + padding * 2
        total_height = rows * card_height + (rows - 1) * spacing + padding * 2

        # Create a new transparent image
        grid_image = Image.new('RGBA', (total_width, total_height), (0, 0, 0, 0))

        # Place each card in the grid
        for i, card_image in enumerate(card_images):
            if i >= num_cards:
                break

            # Calculate position in the grid
            row = i // cols
            col = i % cols

            # Calculate pixel position
            x = padding + col * (card_width + spacing)
            y = padding + row * (card_height + spacing)

            # Paste the card
            if card_image.mode != 'RGBA':
                card_image = card_image.convert('RGBA')

            grid_image.paste(card_image, (x, y), card_image)

        # Calculate scaling to fit standard screen resolution (1920x1080) if needed
        if total_width > 1920:
            scale_factor = 1920 / total_width
            final_width = 1920
            final_height = int(total_height * scale_factor)
            grid_image = grid_image.resize((final_width, final_height), Image.LANCZOS)

        # Save the final image
        grid_image.save(output_path, 'PNG')

        return grid_image



if __name__ == "__main__":
    import argparse
    import datetime

    parser = argparse.ArgumentParser(description='Render card images from JSON data')
    parser.add_argument('--input', default = 'draft_deck.json', help='Input JSON file or directory')
    parser.add_argument('--output-dir', default='final/draft_deck', help='Output directory')
    parser.add_argument('--ppi', type=int, default=900, help='Pixels per inch (300-900)')
    parser.add_argument('--create-tiled', default = False, action='store_true', help='Create tiled image of all cards')
    parser.add_argument('--create-hand', default = True, action='store_true', help='Create sample hand image')
    parser.add_argument('--transparent', default = False, action='store_true', help='Make area outside card border transparent')

    args = parser.parse_args()


    def process_json_file(json_path, renderer, output_dir):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different JSON structures
            cards = []
            if isinstance(data, dict):
                if 'cards' in data:
                    # JSON with a cards array
                    cards = data['cards']
                else:
                    # Single card object
                    cards = [data]
            elif isinstance(data, list):
                # Array of cards
                cards = data

            # Process each card
            for card in cards:
                print(card)
                renderer.render_card(card, output_dir, transparent_outside=args.transparent)

            # Create additional outputs if requested
            if args.create_tiled:
                tiled_output = os.path.join(output_dir, 'tiled_cards.png')
                renderer.create_tiled_image(cards, tiled_output)
                print(f"Created tiled image: {tiled_output}")

            if args.create_hand:
                hand_output = os.path.join(output_dir, 'sample_hand.png')
                renderer.create_sample_hand(cards, hand_output)
                print(f"Created sample hand image: {hand_output}")

        except Exception as e:
            print(f"Error processing {json_path}: {e}")
            raise  # Re-raise to see full error trace


    renderer = CardRenderer(ppi=args.ppi)

    # Handle input path
    input_path = Path(args.input)
    if input_path.is_dir():
        # Process all JSON files in directory
        for json_file in input_path.glob('*.json'):
            process_json_file(json_file, renderer, args.output_dir)
    else:
        # Process single JSON file
        process_json_file(input_path, renderer, args.output_dir)