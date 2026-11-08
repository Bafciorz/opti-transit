package pl.zabawa.optitransit.endpoints;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import pl.zabawa.optitransit.models.dtos.StopDto;

import java.util.List;

@Tag(name = "Stops", description = "Endpoints for searching and inspecting public transit stops")
@RequestMapping("/api/v1/stops")
public interface StopEndpoint {

    @Operation(
            summary = "Search stops by name for autocompletion",
            description = "Returns a list of public transport stops matching the provided search query."
    )
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Successfully retrieved list of matching stops")
    })
    @GetMapping("/search")
    ResponseEntity<List<StopDto>> searchStops(
            @Parameter(description = "Query string to search in stop names", example = "Centrum")
            @RequestParam String query,
            @Parameter(description = "Maximum number of results to return", example = "10")
            @RequestParam(defaultValue = "10") int limit
    );


    @Operation(
            summary = "Get detailed stop info by its GTFS stop ID",
            description = "Returns specific platform/stop details for the given GTFS stop_id."
    )
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Stop found"),
            @ApiResponse(responseCode = "404", description = "Stop not found")
    })
    @GetMapping("/{id}")
    ResponseEntity<StopDto> getStopById(
            @Parameter(description = "GTFS Stop ID", example = "700901")
            @PathVariable String id
    );


    @Operation(
            summary = "Get a sample list of stops",
            description = "Returns a limited list of stops from the database."
    )
    @ApiResponses(value = {
            @ApiResponse(responseCode = "200", description = "Successfully retrieved sample stops")
    })
    @GetMapping
    ResponseEntity<List<StopDto>> getAllStops(
            @Parameter(description = "Maximum number of stops to return", example = "20")
            @RequestParam(defaultValue = "20") int limit
    );
}
