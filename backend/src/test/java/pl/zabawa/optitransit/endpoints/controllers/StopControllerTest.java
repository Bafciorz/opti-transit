package pl.zabawa.optitransit.endpoints.controllers;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.ResponseEntity;
import pl.zabawa.optitransit.models.dtos.DepartureDto;
import pl.zabawa.optitransit.models.dtos.NearbyStopDto;
import pl.zabawa.optitransit.models.dtos.StopDto;
import pl.zabawa.optitransit.services.StopService;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class StopControllerTest {

    @Mock
    private StopService stopService;

    @InjectMocks
    private StopController stopController;

    @Test
    void searchStops_ShouldReturnMatchingStops() {
        // Given
        StopDto stopDto = StopDto.builder()
                .id("700901")
                .name("Centrum")
                .platformCode("01")
                .latitude(52.23)
                .longitude(21.01)
                .build();
        when(stopService.searchStops("Centrum", 10)).thenReturn(List.of(stopDto));

        // When
        ResponseEntity<List<StopDto>> response = stopController.searchStops("Centrum", 10);

        // Then
        assertEquals(200, response.getStatusCode().value());
        assertNotNull(response.getBody());
        assertEquals(1, response.getBody().size());
        assertEquals("Centrum", response.getBody().get(0).getName());
    }

    @Test
    void getNearbyStops_ShouldReturnStopsSortedByDistance() {
        // Given
        NearbyStopDto nearbyStop = NearbyStopDto.builder()
                .id("700901")
                .name("Centrum")
                .latitude(52.2297)
                .longitude(21.0122)
                .distanceMeters(45.5)
                .build();
        when(stopService.getNearbyStops(52.23, 21.01, 500.0, 10))
                .thenReturn(List.of(nearbyStop));

        // When
        ResponseEntity<List<NearbyStopDto>> response = stopController.getNearbyStops(52.23, 21.01, 500.0, 10);

        // Then
        assertEquals(200, response.getStatusCode().value());
        assertNotNull(response.getBody());
        assertEquals(1, response.getBody().size());
        assertEquals("Centrum", response.getBody().get(0).getName());
        assertEquals(45.5, response.getBody().get(0).getDistanceMeters());
    }

    @Test
    void getStopById_WhenFound_ShouldReturn200() {
        // Given
        StopDto stopDto = StopDto.builder().id("700901").name("Centrum").build();
        when(stopService.getStopById("700901")).thenReturn(Optional.of(stopDto));

        // When
        ResponseEntity<StopDto> response = stopController.getStopById("700901");

        // Then
        assertEquals(200, response.getStatusCode().value());
        assertNotNull(response.getBody());
        assertEquals("700901", response.getBody().getId());
    }

    @Test
    void getStopById_WhenNotFound_ShouldReturn404() {
        // Given
        when(stopService.getStopById("non_existing")).thenReturn(Optional.empty());

        // When
        ResponseEntity<StopDto> response = stopController.getStopById("non_existing");

        // Then
        assertEquals(404, response.getStatusCode().value());
    }

    @Test
    void getDepartures_WhenFound_ShouldReturn200() {
        // Given
        DepartureDto departure = DepartureDto.builder()
                .line("179")
                .headsign("Os. Kabaty")
                .departureTime("18:15:00")
                .minutesToDeparture(10L)
                .build();
        when(stopService.getDepartures("700901", "18:05:00", 10)).thenReturn(Optional.of(List.of(departure)));

        // When
        ResponseEntity<List<DepartureDto>> response = stopController.getDepartures("700901", "18:05:00", 10);

        // Then
        assertEquals(200, response.getStatusCode().value());
        assertNotNull(response.getBody());
        assertEquals(1, response.getBody().size());
        assertEquals("179", response.getBody().get(0).getLine());
        assertEquals("Os. Kabaty", response.getBody().get(0).getHeadsign());
    }

    @Test
    void getDepartures_WhenStopNotFound_ShouldReturn404() {
        // Given
        when(stopService.getDepartures("invalid_stop", null, 10)).thenReturn(Optional.empty());

        // When
        ResponseEntity<List<DepartureDto>> response = stopController.getDepartures("invalid_stop", null, 10);

        // Then
        assertEquals(404, response.getStatusCode().value());
    }
}
